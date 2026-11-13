"""Read API of the mock ERP.

No authentication: it is test equipment and listens on localhost only. There
is nothing to write yet; posting invoices comes with the output connectors.
"""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload

from mock_erp import db

Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def _contains(column, text: str):
    return func.lower(column).contains(text.lower(), autoescape=True)


def _supplier(row: db.SupplierRow) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "vat_id": row.vat_id,
        "street": row.street,
        "postcode": row.postcode,
        "city": row.city,
        "email": row.email,
        "ibans": [entry.iban for entry in row.ibans],
    }


def _delivery(row: db.DeliveryRow) -> dict:
    return {
        "id": row.id,
        "po_number": row.po_number,
        "delivery_date": row.delivery_date.isoformat(),
        "lines": [
            {"po_position": line.po_position, "quantity": line.quantity} for line in row.lines
        ],
    }


def _order(row: db.PurchaseOrderRow, *, with_deliveries: bool) -> dict:
    order = {
        "number": row.number,
        "supplier_id": row.supplier_id,
        "order_date": row.order_date.isoformat(),
        "lines": [
            {
                "position": line.position,
                "description": line.description,
                "quantity": line.quantity,
                "unit_code": line.unit_code,
                "unit_price": db.euros(line.unit_price_cents),
                "vat_rate": line.vat_rate,
            }
            for line in row.lines
        ],
    }
    if with_deliveries:
        order["deliveries"] = [_delivery(delivery) for delivery in row.deliveries]
    return order


def _transaction(row: db.BankTransactionRow) -> dict:
    return {
        "id": row.id,
        "booking_date": row.booking_date.isoformat(),
        "amount": db.euros(row.amount_cents),
        "counterparty_name": row.counterparty_name,
        "counterparty_iban": row.counterparty_iban,
        "purpose": row.purpose,
    }


def create_app(engine: Engine) -> FastAPI:
    app = FastAPI(title="mock ERP")

    def session() -> Iterator[Session]:
        with Session(engine) as active:
            yield active

    Db = Annotated[Session, Depends(session)]

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/suppliers")
    def suppliers(
        active: Db,
        vat_id: str | None = None,
        iban: str | None = None,
        q: Annotated[str | None, Query(description="part of the name")] = None,
        limit: Limit = 50,
        offset: Offset = 0,
    ) -> dict:
        query = select(db.SupplierRow).options(selectinload(db.SupplierRow.ibans))
        if vat_id is not None:
            query = query.where(db.SupplierRow.vat_id == vat_id)
        if iban is not None:
            query = query.where(db.SupplierRow.ibans.any(db.SupplierIbanRow.iban == iban))
        if q:
            query = query.where(_contains(db.SupplierRow.name, q))
        rows = active.scalars(query.order_by(db.SupplierRow.id).limit(limit).offset(offset))
        return {"suppliers": [_supplier(row) for row in rows]}

    @app.get("/suppliers/{supplier_id}")
    def supplier(supplier_id: str, active: Db) -> dict:
        row = active.get(db.SupplierRow, supplier_id)
        if row is None:
            raise HTTPException(404, "supplier not found")
        return _supplier(row)

    @app.get("/purchase-orders")
    def purchase_orders(
        active: Db,
        supplier_id: str | None = None,
        q: Annotated[str | None, Query(description="part of the order number")] = None,
        limit: Limit = 50,
        offset: Offset = 0,
    ) -> dict:
        query = select(db.PurchaseOrderRow).options(selectinload(db.PurchaseOrderRow.lines))
        if supplier_id is not None:
            query = query.where(db.PurchaseOrderRow.supplier_id == supplier_id)
        if q:
            query = query.where(_contains(db.PurchaseOrderRow.number, q))
        rows = active.scalars(
            query.order_by(db.PurchaseOrderRow.number).limit(limit).offset(offset)
        )
        return {"purchase_orders": [_order(row, with_deliveries=False) for row in rows]}

    @app.get("/purchase-orders/{number}")
    def purchase_order(number: str, active: Db) -> dict:
        row = active.get(db.PurchaseOrderRow, number)
        if row is None:
            raise HTTPException(404, "purchase order not found")
        return _order(row, with_deliveries=True)

    @app.get("/deliveries")
    def deliveries(
        active: Db,
        po_number: str | None = None,
        supplier_id: str | None = None,
        limit: Limit = 50,
        offset: Offset = 0,
    ) -> dict:
        query = select(db.DeliveryRow).options(selectinload(db.DeliveryRow.lines))
        if po_number is not None:
            query = query.where(db.DeliveryRow.po_number == po_number)
        if supplier_id is not None:
            query = query.join(db.DeliveryRow.order).where(
                db.PurchaseOrderRow.supplier_id == supplier_id
            )
        rows = active.scalars(query.order_by(db.DeliveryRow.id).limit(limit).offset(offset))
        return {"deliveries": [_delivery(row) for row in rows]}

    @app.get("/bank-transactions")
    def bank_transactions(
        active: Db,
        iban: str | None = None,
        amount: Decimal | None = None,
        amount_min: Decimal | None = None,
        amount_max: Decimal | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        q: Annotated[str | None, Query(description="part of the purpose or the name")] = None,
        limit: Limit = 50,
        offset: Offset = 0,
    ) -> dict:
        query = select(db.BankTransactionRow)
        if iban is not None:
            query = query.where(db.BankTransactionRow.counterparty_iban == iban)
        try:
            if amount is not None:
                query = query.where(db.BankTransactionRow.amount_cents == db.cents(amount))
            if amount_min is not None:
                query = query.where(db.BankTransactionRow.amount_cents >= db.cents(amount_min))
            if amount_max is not None:
                query = query.where(db.BankTransactionRow.amount_cents <= db.cents(amount_max))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if date_from is not None:
            query = query.where(db.BankTransactionRow.booking_date >= date_from)
        if date_to is not None:
            query = query.where(db.BankTransactionRow.booking_date <= date_to)
        if q:
            query = query.where(
                or_(
                    _contains(db.BankTransactionRow.purpose, q),
                    _contains(db.BankTransactionRow.counterparty_name, q),
                )
            )
        rows = active.scalars(query.order_by(db.BankTransactionRow.id).limit(limit).offset(offset))
        return {"bank_transactions": [_transaction(row) for row in rows]}

    return app


def create_app_from_env() -> FastAPI:
    """For `uvicorn --factory mock_erp.main:create_app_from_env`."""
    return create_app(db.engine_for(db.database_url()))
