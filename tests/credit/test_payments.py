"""How a credit note is settled, and an invoice paid less one (v1.0 Stage 5,
increment 4): a refund from the supplier, or a credit offset in a transfer
that pays an invoice less the credit. Matched like payments: on a reference
when the transfer names the document, on the amount alone otherwise."""

from datetime import date
from decimal import Decimal

from app.extract.draft import InvoiceDraft
from app.matching import Basis, match_credit_payments
from app.matching.matcher import _match_payments
from app.matching.model import BankTransaction, Payment, PeerInvoice

SUPPLIER_IBAN = "DE02120300000000202051"
INVOICE = InvoiceDraft(
    number="RE-2026-100",
    type_code="380",
    issue_date=date(2026, 3, 1),
    gross_total=Decimal("119.00"),
)
CREDIT = InvoiceDraft(
    number="GS-2026-7",
    type_code="381",
    issue_date=date(2026, 3, 10),
    gross_total=Decimal("21.42"),
    preceding_ref="RE-2026-100",
)


def transfer(id_: str, amount: str, purpose: str = "", day: int = 20) -> BankTransaction:
    return BankTransaction(
        id=id_,
        booking_date=date(2026, 3, day),
        amount=Decimal(amount),
        counterparty_name="Muster Elektro GmbH",
        counterparty_iban=SUPPLIER_IBAN,
        purpose=purpose,
    )


def test_a_refund_that_names_the_credit_note_settles_it():
    payments, basis, notes = match_credit_payments(
        CREDIT, [transfer("TX-1", "-21.42", "Erstattung GS-2026-7")], ()
    )
    assert (payments, basis) == ((Payment("TX-1", Decimal("21.42")),), Basis.EXACT_REFERENCE)
    assert notes[0].params["note"].code == "match.refund"


def test_a_refund_of_the_amount_alone_is_matched_on_content():
    _, basis, _ = match_credit_payments(CREDIT, [transfer("TX-1", "-21.42", "Gutschrift")], ())
    assert basis is Basis.CONTENT


def test_two_refunds_of_the_same_amount_leave_the_choice_to_a_person():
    payments, basis, _ = match_credit_payments(
        CREDIT, [transfer("TX-1", "-21.42"), transfer("TX-2", "-21.42")], ()
    )
    assert (payments, basis) == ((), Basis.AMBIGUOUS)


def test_a_credit_offset_in_the_payment_of_its_invoice():
    peers = (PeerInvoice("RE-2026-100", Decimal("119.00")),)
    payments, basis, notes = match_credit_payments(
        CREDIT, [transfer("TX-9", "97.58", "RE-2026-100 abzgl. GS-2026-7")], peers
    )
    assert (payments, basis) == ((Payment("TX-9", Decimal("21.42")),), Basis.EXACT_REFERENCE)
    assert notes[0].params["note"].code == "match.credit_offset"


def test_nothing_found_says_so():
    payments, basis, notes = match_credit_payments(CREDIT, [transfer("TX-1", "50.00")], ())
    assert (payments, basis, notes[0].code) == ((), Basis.NONE, "match.no_refund")


def test_an_invoice_paid_less_a_credit_note_is_settled_by_both():
    peers = (PeerInvoice("GS-2026-7", Decimal("21.42"), credit=True),)
    payments, basis, notes = _match_payments(
        INVOICE, [transfer("TX-9", "97.58", "RE-2026-100 abzgl. GS-2026-7")], peers
    )
    assert payments == (
        Payment("TX-9", Decimal("97.58")),
        Payment("TX-9", Decimal("21.42"), credit_note="GS-2026-7"),
    )
    assert sum(p.amount for p in payments) == INVOICE.gross_total
    assert basis is Basis.EXACT_REFERENCE
    assert notes[0].params["note"].code == "match.transfer_offset"


def test_a_refund_never_pays_an_invoice():
    # 140.42 out and 21.42 back would add up to the invoice total.
    payments, basis, _ = _match_payments(
        INVOICE, [transfer("TX-1", "140.42", "RE-2026-100"), transfer("TX-2", "-21.42")], ()
    )
    assert (payments, basis) == ((), Basis.NONE)


def test_a_full_credit_of_the_same_total_does_not_make_the_payment_ambiguous():
    # Only the amount speaks for TX-1; another *invoice* of 119.00 would make
    # it ambiguous, the credit note of 119.00 does not.
    full = PeerInvoice("GS-2026-8", Decimal("119.00"), credit=True)
    payments, basis, _ = _match_payments(INVOICE, [transfer("TX-1", "119.00", "Zahlung")], (full,))
    assert (payments, basis) == ((Payment("TX-1", Decimal("119.00")),), Basis.CONTENT)
    twin = PeerInvoice("RE-2026-101", Decimal("119.00"))
    _, basis, _ = _match_payments(INVOICE, [transfer("TX-1", "119.00", "Zahlung")], (twin,))
    assert basis is Basis.AMBIGUOUS


def test_a_transfer_that_names_a_credit_note_besides_the_invoice_still_pays_it():
    peers = (PeerInvoice("GS-2026-7", Decimal("5.00"), credit=True),)
    payments, basis, _ = _match_payments(
        INVOICE, [transfer("TX-1", "119.00", "RE-2026-100 GS-2026-7")], peers
    )
    assert (payments, basis) == ((Payment("TX-1", Decimal("119.00")),), Basis.EXACT_REFERENCE)
