"""Small helpers shared by the UBL and CII parsers: read a value at a path,
convert it, and fail with the business-term name when it is missing or bad."""

import re
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from lxml import etree


class InvoiceParseError(ValueError):
    """The document is not parseable as the invoice format it claims to be."""


_WHITESPACE = re.compile(r"\s+")


class Reader:
    def __init__(self, namespaces: dict[str, str]) -> None:
        self._ns = namespaces

    def find(self, node: etree._Element, path: str) -> etree._Element | None:
        return node.find(path, self._ns)

    def findall(self, node: etree._Element, path: str) -> list[etree._Element]:
        return node.findall(path, self._ns)

    def text(self, node: etree._Element, path: str) -> str | None:
        value = node.findtext(path, namespaces=self._ns)
        if value is None:
            return None
        value = value.strip()
        return value or None

    def required(self, node: etree._Element, path: str, field: str) -> str:
        value = self.text(node, path)
        if value is None:
            raise InvoiceParseError(f"missing {field} (expected at {path})")
        return value

    def amount(self, node: etree._Element, path: str, field: str) -> Decimal:
        return to_amount(self.required(node, path, field), field)

    def optional_amount(self, node: etree._Element, path: str, field: str) -> Decimal | None:
        value = self.text(node, path)
        return None if value is None else to_amount(value, field)


def to_amount(value: str, field: str) -> Decimal:
    try:
        amount = Decimal(value.strip())
    except InvalidOperation as exc:
        raise InvoiceParseError(f"{field} is not a number: {value!r}") from exc
    if not amount.is_finite():
        raise InvoiceParseError(f"{field} is not a finite number: {value!r}")
    return amount


def to_date(value: str, fmt: str, field: str) -> date:
    try:
        return datetime.strptime(value.strip(), fmt).date()  # noqa: DTZ007 - calendar date only
    except ValueError as exc:
        raise InvoiceParseError(f"{field} is not a valid date: {value!r}") from exc


def compact_id(value: str | None) -> str | None:
    """Normalize identifiers written with optional spaces (VAT ID, IBAN)."""
    if value is None:
        return None
    return _WHITESPACE.sub("", value).upper() or None


def compact_ids(values: Iterable[str | None]) -> tuple[str, ...]:
    """Normalized, de-duplicated identifiers in document order."""
    seen = dict.fromkeys(compact_id(value) for value in values)
    seen.pop(None, None)
    return tuple(seen)
