"""The invoice a credit note corrects (v1.0 Stage 5, increment 2): named in
BT-25, or found by supplier and total or by its lines; never another credit
note, a rejected invoice or another supplier's."""

from datetime import date
from decimal import Decimal

from app.checks import FindingType
from app.checks.context import CheckContext, KnownInvoice, KnownLine, MasterData
from app.checks.credit import link_credit
from app.checks.rules import run_checks
from app.extract.draft import InvoiceDraft, LineDraft, PartyDraft
from app.matching import Basis

SELLER = PartyDraft(name="Muster Elektro GmbH", vat_id="DE123456789")
CABLE = KnownLine("Kabel NYM-J 3x1,5", Decimal("100"), Decimal("0.90"))
SOCKET = KnownLine("Steckdose", Decimal("10"), Decimal("4.50"))


def invoice(number: str, gross: str, **changes) -> KnownInvoice:
    values = {
        "document_id": f"doc-{number}",
        "seller_name": SELLER.name,
        "seller_vat_id": SELLER.vat_id,
        "number": number,
        "issue_date": date(2026, 3, 1),
        "gross_total": Decimal(gross),
        "type_code": "380",
        "lines": (CABLE, SOCKET),
    }
    return KnownInvoice(**(values | changes))


def credit(gross: str = "119.00", **changes) -> InvoiceDraft:
    values = {
        "number": "GS-1",
        "type_code": "381",
        "issue_date": date(2026, 3, 20),
        "seller": SELLER,
        "gross_total": Decimal(gross),
    }
    return InvoiceDraft(**(values | changes))


def test_an_invoice_is_not_linked_to_anything():
    assert link_credit(credit(type_code="380"), (invoice("RE-1", "119.00"),)) is None


def test_the_invoice_the_credit_note_names_is_linked():
    known = (invoice("RE-1", "500.00"), invoice("RE-2", "119.00"))
    link = link_credit(credit(preceding_ref="re 1"), known)
    assert (link.basis, link.invoice.number) == (Basis.EXACT_REFERENCE, "RE-1")
    assert link.explanation.code == "credit.reference"


def test_a_named_invoice_not_on_file_is_not_guessed_away():
    # RE-2 has the same total, but the credit note says RE-9.
    link = link_credit(credit(preceding_ref="RE-9"), (invoice("RE-2", "119.00"),))
    assert (link.basis, link.invoice) == (Basis.NONE, None)
    assert link.explanation.code == "credit.reference_not_found"


def test_without_a_reference_the_invoice_of_the_same_total_is_linked():
    link = link_credit(credit(), (invoice("RE-1", "500.00"), invoice("RE-2", "119.00")))
    assert (link.basis, link.invoice.number) == (Basis.CONTENT, "RE-2")
    assert link.explanation.code == "credit.by_total"


def test_a_partial_credit_is_linked_by_the_lines_it_takes_back():
    returned = LineDraft(
        description="Kabel NYM-J 3x1,5", quantity=Decimal("20"), unit_price=Decimal("0.90")
    )
    link = link_credit(
        credit("21.42", lines=(returned,)),
        (invoice("RE-1", "119.00"), invoice("RE-2", "500.00", lines=(SOCKET,))),
    )
    assert (link.basis, link.invoice.number) == (Basis.CONTENT, "RE-1")
    assert link.explanation.code == "credit.by_lines"


def test_more_than_was_billed_is_not_a_line_of_the_invoice():
    returned = LineDraft(
        description="Steckdose", quantity=Decimal("11"), unit_price=Decimal("4.50")
    )
    link = link_credit(credit("58.91", lines=(returned,)), (invoice("RE-1", "119.00"),))
    assert link.basis is Basis.NONE


def test_two_invoices_of_the_same_total_leave_the_choice_to_a_person():
    link = link_credit(credit(), (invoice("RE-1", "119.00"), invoice("RE-2", "119.00")))
    assert (link.basis, link.invoice) == (Basis.AMBIGUOUS, None)
    assert link.explanation.params["numbers"] == ["RE-1", "RE-2"]


def test_only_this_suppliers_invoices_count():
    other = invoice("RE-1", "119.00", seller_vat_id="DE999999999", seller_name="Andere GmbH")
    assert link_credit(credit(), (other,)).basis is Basis.NONE


def test_another_credit_note_or_a_rejected_invoice_is_not_corrected():
    known = (
        invoice("GS-0", "119.00", type_code="381"),
        invoice("RE-1", "119.00", rejected=True),
    )
    assert link_credit(credit(), known).basis is Basis.NONE


def test_an_invoice_issued_after_the_credit_note_is_not_found_by_content():
    later = invoice("RE-1", "119.00", issue_date=date(2026, 4, 1))
    assert link_credit(credit(), (later,)).basis is Basis.NONE


def _findings(draft: InvoiceDraft, known: tuple[KnownInvoice, ...], **context) -> dict:
    link = link_credit(draft, known)
    found = run_checks(
        draft,
        CheckContext(
            master_data=MasterData.AVAILABLE, known_invoices=known, credit=link, **context
        ),
    )
    return {finding.type: finding for finding in found}


def test_a_credit_note_without_its_invoice_is_a_finding():
    findings = _findings(credit(preceding_ref="RE-9"), ())
    assert findings[FindingType.CREDIT_NOT_LINKED].message.code == "credit.reference_not_found"
    # Still a credit note, which a person books (D9 until Stage 5, increment 3).
    assert FindingType.CREDIT_NOTE in findings


def test_a_credit_for_more_than_is_left_of_the_invoice_is_a_finding():
    from dataclasses import replace

    draft = credit("80.00", preceding_ref="RE-1")
    link = replace(
        link_credit(draft, (invoice("RE-1", "119.00"),)), credited_before=Decimal("50.00")
    )
    found = run_checks(draft, CheckContext(master_data=MasterData.AVAILABLE, credit=link))
    exceeds = [f for f in found if f.type is FindingType.CREDIT_EXCEEDS_INVOICE]
    assert len(exceeds) == 1
    assert exceeds[0].message.params["open_amount"] == Decimal("69.00")
    assert exceeds[0].message.params["credited_amount"] == Decimal("50.00")


def test_a_credit_within_what_is_left_is_no_finding_about_its_invoice():
    findings = _findings(credit(preceding_ref="RE-1"), (invoice("RE-1", "119.00"),))
    assert FindingType.CREDIT_NOT_LINKED not in findings
    assert FindingType.CREDIT_EXCEEDS_INVOICE not in findings


def test_a_credit_note_is_no_duplicate_of_its_invoice_and_needs_no_order():
    # Same supplier, same total, same date: the duplicate rule must not see a copy.
    same_day = credit(issue_date=date(2026, 3, 1), preceding_ref="RE-1")
    findings = _findings(same_day, (invoice("RE-1", "119.00"),))
    assert FindingType.NEAR_DUPLICATE not in findings
    assert FindingType.MISSING_PO not in findings
