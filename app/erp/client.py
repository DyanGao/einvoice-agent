"""Reading master data from the ERP.

Only reads: nothing in this module can change the ERP. Posting invoices is the
connectors' job and happens after a decision, never from here.
"""

import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Protocol
from urllib.parse import quote

import httpx

from app.checks.context import PoLine, PurchaseOrder, Supplier
from app.matching.model import BankTransaction, Delivery, DeliveryLine, Order, OrderLine

PAGE = 200

_NOT_ALNUM = re.compile(r"[^a-z0-9]")


class ErpUnavailable(RuntimeError):
    """The ERP could not be reached or answered with an error."""


class ErpClient(Protocol):
    def find_supplier(self, vat_id: str | None, name: str | None) -> Supplier | None: ...

    def get_purchase_order(self, number: str) -> PurchaseOrder | None: ...


def _name_key(name: str) -> str:
    return _NOT_ALNUM.sub("", name.casefold())


def _supplier(data: dict) -> Supplier:
    return Supplier(
        id=data["id"], name=data["name"], vat_id=data["vat_id"], ibans=tuple(data["ibans"])
    )


class HttpErpClient:
    """The ERP's read API (see mock_erp/main.py for the mock's version of it)."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        """The JSON answer, or None for 404."""
        try:
            response = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise ErpUnavailable(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ErpUnavailable(f"ERP answered {response.status_code} for {path}")
        try:
            return response.json()
        except ValueError as exc:
            raise ErpUnavailable(f"ERP answer for {path} is not JSON") from exc

    def _all(self, path: str, params: dict, name: str) -> list[dict]:
        """Every row of a paged list."""
        rows: list[dict] = []
        while True:
            page = self._get(path, {**params, "limit": PAGE, "offset": len(rows)})
            if page is None:
                raise ErpUnavailable(f"ERP has no {path}")
            rows += page[name]
            if len(page[name]) < PAGE:
                return rows

    def orders_of(self, supplier_id: str) -> list[Order]:
        """The supplier's purchase orders, each with its deliveries."""
        deliveries: dict[str, list[Delivery]] = {}
        for row in self._all("/deliveries", {"supplier_id": supplier_id}, "deliveries"):
            deliveries.setdefault(row["po_number"], []).append(
                Delivery(
                    id=row["id"],
                    po_number=row["po_number"],
                    delivery_date=date.fromisoformat(row["delivery_date"]),
                    lines=tuple(
                        DeliveryLine(line["po_position"], Decimal(line["quantity"]))
                        for line in row["lines"]
                    ),
                )
            )
        return [
            Order(
                number=row["number"],
                supplier_id=row["supplier_id"],
                order_date=date.fromisoformat(row["order_date"]),
                lines=tuple(
                    OrderLine(
                        position=line["position"],
                        description=line["description"],
                        quantity=Decimal(line["quantity"]),
                        unit_price=Decimal(line["unit_price"]),
                    )
                    for line in row["lines"]
                ),
                deliveries=tuple(deliveries.get(row["number"], ())),
            )
            for row in self._all(
                "/purchase-orders", {"supplier_id": supplier_id}, "purchase_orders"
            )
        ]

    def transactions_to(self, ibans: Sequence[str]) -> list[BankTransaction]:
        """Outgoing payments to any of these accounts."""
        return [
            BankTransaction(
                id=row["id"],
                booking_date=date.fromisoformat(row["booking_date"]),
                amount=Decimal(row["amount"]),
                counterparty_name=row["counterparty_name"],
                counterparty_iban=row["counterparty_iban"],
                purpose=row["purpose"],
            )
            for iban in ibans
            for row in self._all("/bank-transactions", {"iban": iban}, "bank_transactions")
        ]

    def transactions_since(self, since: date | None) -> list[BankTransaction]:
        """Every outgoing payment booked on or after `since` (all if None)."""
        params = {} if since is None else {"date_from": since.isoformat()}
        return [
            BankTransaction(
                id=row["id"],
                booking_date=date.fromisoformat(row["booking_date"]),
                amount=Decimal(row["amount"]),
                counterparty_name=row["counterparty_name"],
                counterparty_iban=row["counterparty_iban"],
                purpose=row["purpose"],
            )
            for row in self._all("/bank-transactions", params, "bank_transactions")
        ]

    def supplier_with_iban(self, iban: str) -> Supplier | None:
        """The supplier this account is on file for, if exactly one."""
        found = self._get("/suppliers", {"iban": iban})["suppliers"]
        return _supplier(found[0]) if len(found) == 1 else None

    def find_supplier(self, vat_id: str | None, name: str | None) -> Supplier | None:
        """By VAT ID if the invoice has one; otherwise by name, and then only
        if exactly one supplier carries that name."""
        if vat_id:
            found = self._get("/suppliers", {"vat_id": vat_id})["suppliers"]
            if found:
                return _supplier(found[0])
        if name and _name_key(name):
            candidates = self._get("/suppliers", {"q": name, "limit": 200})["suppliers"]
            same = [entry for entry in candidates if _name_key(entry["name"]) == _name_key(name)]
            if len(same) == 1:
                return _supplier(same[0])
        return None

    def get_purchase_order(self, number: str) -> PurchaseOrder | None:
        # The number comes from an invoice: it must not be able to change the
        # path or add a query. Quoting covers everything but the dot segments.
        if number in ("", ".", ".."):
            return None
        data = self._get(f"/purchase-orders/{quote(number, safe='')}")
        if data is None:
            return None
        return PurchaseOrder(
            number=data["number"],
            lines=tuple(
                PoLine(
                    description=line["description"],
                    quantity=Decimal(line["quantity"]),
                    unit_price=Decimal(line["unit_price"]),
                )
                for line in data["lines"]
            ),
        )
