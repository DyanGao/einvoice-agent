"""Refunds and offsets through the checks, the database and the bank watch
(v1.0 Stage 5, increment 4), against a small ERP in memory."""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from app import db
from app.checks.context import Supplier
from app.checks.service import Checker
from app.ingest import ingest
from app.matching.model import BankTransaction
from app.rematch import fully_paid, watch_bank
from eval.generators.canonical import XRECHNUNG, build_invoice, credit_note_for
from eval.generators.xrechnung import render_ubl
from tests.support import inline_queue


@dataclass
class Erp:
    """The supplier of the generated invoice, no orders, and a bank."""

    supplier: Supplier
    transactions: list[BankTransaction] = field(default_factory=list)

    def find_supplier(self, vat_id, name):
        return self.supplier if vat_id == self.supplier.vat_id else None

    def orders_of(self, supplier_id):
        return []

    def transactions_to(self, ibans):
        return [t for t in self.transactions if t.counterparty_iban in ibans]

    def transactions_since(self, since):
        return list(self.transactions)

    def supplier_with_iban(self, iban):
        return self.supplier if iban in self.supplier.ibans else None


def setup(session_factory):
    invoice = build_invoice(41, 3)
    # A partial credit: the first line taken back.
    credit = credit_note_for(invoice, "GS-2026-00041", items=invoice.items[:1])
    supplier = Supplier("S1", invoice.seller.name, invoice.seller.vat_id, ibans=(invoice.ibans[0],))
    erp = Erp(supplier)
    queue = inline_queue(session_factory, checker=Checker(erp))

    def send(document, name):
        return ingest(session_factory, queue, render_ubl(document, XRECHNUNG), name).document_id

    return invoice, credit, erp, send


def document(session_factory, document_id):
    session = session_factory()
    return session, session.get(db.DocumentRow, document_id)


def test_an_invoice_paid_less_its_credit_note_is_settled_and_so_is_the_credit(session_factory):
    invoice, credit, erp, send = setup(session_factory)
    erp.transactions.append(
        BankTransaction(
            "TX-1",
            credit.issue_date + timedelta(days=2),
            invoice.gross_total - credit.gross_total,
            "Lieferant",
            erp.supplier.ibans[0],
            f"{invoice.number} abzgl. {credit.number}",
        )
    )
    invoice_id = send(invoice, "re.xml")
    credit_id = send(credit, "gs.xml")
    # The invoice was checked before its credit note came in; the credit note's
    # arrival matches the supplier's open invoices again (app/rematch.py).
    session, stored = document(session_factory, invoice_id)
    with session:
        parts = stored.match.payments
        assert [p.get("credit_note") for p in parts] == [None, credit.number]
        assert sum(Decimal(p["amount"]) for p in parts) == invoice.gross_total
        assert fully_paid(stored)
    session, stored = document(session_factory, credit_id)
    with session:
        assert stored.match.payments == [
            {"bank_transaction_id": "TX-1", "amount": str(credit.gross_total)}
        ]
        assert stored.match.payment_basis == "exact_reference"
        assert fully_paid(stored)


def test_a_refund_that_arrives_later_is_found_by_the_bank_watch(session_factory):
    invoice, credit, erp, send = setup(session_factory)
    send(invoice, "re.xml")
    credit_id = send(credit, "gs.xml")
    session, stored = document(session_factory, credit_id)
    with session:
        assert stored.match.payments == []
        assert not fully_paid(stored)
    erp.transactions.append(
        BankTransaction(
            "TX-7",
            credit.issue_date + timedelta(days=5),
            -credit.gross_total,
            "Lieferant",
            erp.supplier.ibans[0],
            f"Erstattung {credit.number}",
        )
    )
    with session_factory.begin() as session:
        watch_bank(session, erp, Checker(erp), db.DEFAULT_TENANT_ID)
    session, stored = document(session_factory, credit_id)
    with session:
        assert [p["bank_transaction_id"] for p in stored.match.payments] == ["TX-7"]
        assert fully_paid(stored)
