"""The mock ERP's own tables. Not part of the application's schema and not
managed by its migrations: the tables are created when the ERP is seeded.

Money is stored in cents and quantities as text, so that nothing is rounded
by the database.
"""

import os
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, String, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

DEFAULT_DATABASE_URL = "sqlite:///data/mock_erp/erp.sqlite3"


def database_url() -> str:
    return os.environ.get("MOCK_ERP_DATABASE_URL", DEFAULT_DATABASE_URL)


def engine_for(url: str) -> Engine:
    return create_engine(url)


def cents(amount: Decimal) -> int:
    scaled = amount * 100
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{amount} has more than two decimal places")
    return int(scaled)


def euros(amount_cents: int) -> str:
    return str((Decimal(amount_cents) / 100).quantize(Decimal("0.01")))


class Base(DeclarativeBase):
    pass


class SupplierRow(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    vat_id: Mapped[str | None] = mapped_column(String(20), index=True)
    street: Mapped[str] = mapped_column(String(200))
    postcode: Mapped[str] = mapped_column(String(10))
    city: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200))

    ibans: Mapped[list["SupplierIbanRow"]] = relationship(
        order_by="SupplierIbanRow.position", cascade="all, delete-orphan"
    )


class SupplierIbanRow(Base):
    __tablename__ = "supplier_ibans"

    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    iban: Mapped[str] = mapped_column(String(34), index=True)


class PurchaseOrderRow(Base):
    __tablename__ = "purchase_orders"

    number: Mapped[str] = mapped_column(String(20), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), index=True)
    order_date: Mapped[date] = mapped_column(Date)

    lines: Mapped[list["OrderLineRow"]] = relationship(
        order_by="OrderLineRow.position", cascade="all, delete-orphan"
    )
    deliveries: Mapped[list["DeliveryRow"]] = relationship(
        order_by="DeliveryRow.delivery_date", back_populates="order"
    )


class OrderLineRow(Base):
    __tablename__ = "purchase_order_lines"

    po_number: Mapped[str] = mapped_column(ForeignKey("purchase_orders.number"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    description: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[str] = mapped_column(String(20))
    unit_code: Mapped[str] = mapped_column(String(3))
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    vat_rate: Mapped[str] = mapped_column(String(6))


class DeliveryRow(Base):
    __tablename__ = "deliveries"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    po_number: Mapped[str] = mapped_column(ForeignKey("purchase_orders.number"), index=True)
    delivery_date: Mapped[date] = mapped_column(Date)

    order: Mapped[PurchaseOrderRow] = relationship(back_populates="deliveries")
    lines: Mapped[list["DeliveryLineRow"]] = relationship(
        order_by="DeliveryLineRow.po_position", cascade="all, delete-orphan"
    )


class DeliveryLineRow(Base):
    __tablename__ = "delivery_lines"

    delivery_id: Mapped[str] = mapped_column(ForeignKey("deliveries.id"), primary_key=True)
    po_position: Mapped[int] = mapped_column(Integer, primary_key=True)
    quantity: Mapped[str] = mapped_column(String(20))


class BankTransactionRow(Base):
    """An outgoing payment. The amount is positive."""

    __tablename__ = "bank_transactions"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    booking_date: Mapped[date] = mapped_column(Date, index=True)
    amount_cents: Mapped[int] = mapped_column(Integer, index=True)
    counterparty_name: Mapped[str] = mapped_column(String(200))
    counterparty_iban: Mapped[str] = mapped_column(String(34), index=True)
    purpose: Mapped[str] = mapped_column(String(300))


class BookedInvoiceRow(Base):
    """Invoices posted to the ERP. Empty until the output connectors exist."""

    __tablename__ = "booked_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), index=True)
    number: Mapped[str] = mapped_column(String(50))
    gross_cents: Mapped[int] = mapped_column(Integer)
