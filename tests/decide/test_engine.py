from datetime import date
from decimal import Decimal

import pytest

from app.checks import Finding, FindingType, Severity
from app.decide import Cause, DecisionSettings, Route, decide, reading_causes
from app.extract import InvoiceDraft, LineDraft, PartyDraft, VatBreakdownDraft
from app.matching import Basis, Match
from app.models import DocumentFormat
from app.pipeline import DocumentStatus

SETTINGS = DecisionSettings(min_field_confidence=0.9)
MATCHED = Match(
    supplier_id="S1",
    po_number="PO-1",
    po_basis=Basis.EXACT_REFERENCE,
    delivery_id="LS-1",
)


def invoice(**fields) -> InvoiceDraft:
    """A complete invoice: 2 x 50.00 at 19 %."""
    base = dict(
        number="RE-100",
        issue_date=date(2026, 3, 1),
        type_code="380",
        currency="EUR",
        po_ref="PO-1",
        seller=PartyDraft(name="Muster GmbH", vat_id="DE123456788"),
        buyer=PartyDraft(name="Nordlicht Handel GmbH"),
        payment_ibans=("DE89370400440532013000",),
        lines=(
            LineDraft(
                line_id="1",
                description="Papier",
                quantity=Decimal(2),
                unit_price=Decimal("50.00"),
                net=Decimal("100.00"),
                vat_rate=Decimal(19),
            ),
        ),
        vat_breakdown=(
            VatBreakdownDraft(
                category="S",
                rate=Decimal(19),
                taxable_amount=Decimal("100.00"),
                tax_amount=Decimal("19.00"),
            ),
        ),
        line_net_total=Decimal("100.00"),
        net_total=Decimal("100.00"),
        vat_total=Decimal("19.00"),
        gross_total=Decimal("119.00"),
        amount_due=Decimal("119.00"),
        source_format=DocumentFormat.UBL,
    )
    return InvoiceDraft(**(base | fields))


def run(
    draft=None,
    *,
    status=DocumentStatus.VALID,
    findings=(),
    match=MATCHED,
    corrected=False,
    settings=SETTINGS,
):
    return decide(
        draft or invoice(),
        status=status,
        findings=findings,
        match=match,
        corrected=corrected,
        settings=settings,
    )


def causes(decision) -> list[Cause]:
    return [reason.cause for reason in decision.reasons]


def finding(kind: FindingType, severity: Severity = Severity.REVIEW) -> Finding:
    return Finding(type=kind, severity=severity, message=f"{kind.value} message")


def test_valid_e_invoice_on_a_matched_order_and_delivery_is_booked_without_review():
    decision = run()
    assert (decision.route, decision.reasons) == (Route.AUTO, ())


def test_extracted_invoice_read_with_high_confidence_is_booked_without_review():
    draft = invoice(field_confidence={"number": 0.99, "gross_total": 0.95})
    assert run(draft, status=DocumentStatus.EXTRACTED).route is Route.AUTO


def test_field_below_the_threshold_sends_to_review_and_is_named():
    draft = invoice(field_confidence={"number": 0.99, "due_date": 0.4})
    decision = run(draft, status=DocumentStatus.EXTRACTED)
    (reason,) = decision.reasons
    assert (decision.route, reason.cause, reason.fields) == (
        Route.REVIEW,
        Cause.LOW_CONFIDENCE,
        ("due_date",),
    )


def test_confidence_exactly_at_the_threshold_passes():
    draft = invoice(field_confidence={"number": 0.9})
    assert run(draft, status=DocumentStatus.EXTRACTED).route is Route.AUTO


@pytest.mark.parametrize(
    "status",
    [
        DocumentStatus.INVALID,
        DocumentStatus.UNRECOGNIZED,
        DocumentStatus.VALIDATION_FAILED,
        DocumentStatus.PARSE_FAILED,
        DocumentStatus.EXTRACTION_FAILED,
    ],
)
def test_document_that_was_not_validated_or_read_goes_to_review(status):
    assert causes(run(status=status)) == [Cause.NOT_READ]


def test_missing_mandatory_field_goes_to_review_and_is_named():
    (reason,) = run(invoice(number=None), status=DocumentStatus.EXTRACTED).reasons
    assert (reason.cause, reason.fields) == (Cause.MISSING_FIELDS, ("number",))


def test_assumed_value_goes_to_review_however_sure_the_rest_is():
    """The EUR default is an assumption, not a reading (complete.py)."""
    draft = invoice(derived_fields=("currency",), field_confidence={"currency": 1.0})
    (reason,) = run(draft, status=DocumentStatus.EXTRACTED).reasons
    assert (reason.cause, reason.fields) == (Cause.ASSUMED_VALUE, ("currency",))


def test_value_computed_from_other_values_is_not_an_assumption():
    draft = invoice(derived_fields=("vat_total",), field_confidence={"vat_total": 0.99})
    assert run(draft, status=DocumentStatus.EXTRACTED).route is Route.AUTO


@pytest.mark.parametrize(
    "kind",
    [FindingType.IBAN_CHANGED, FindingType.CREDIT_NOTE, FindingType.DUPLICATE],
)
def test_finding_that_needs_a_person_goes_to_review_even_at_full_confidence(kind):
    """ARCHITECTURE §6: a changed bank account always goes to review; D9:
    so does a credit note."""
    draft = invoice(field_confidence={"number": 1.0, "gross_total": 1.0})
    decision = run(draft, status=DocumentStatus.EXTRACTED, findings=(finding(kind),))
    (reason,) = decision.reasons
    assert (decision.route, reason.cause, reason.findings) == (
        Route.REVIEW,
        Cause.FINDINGS,
        (kind.value,),
    )


def test_finding_for_information_does_not_stop_the_invoice():
    note = finding(FindingType.PO_NOT_QUOTED, Severity.INFO)
    assert run(findings=(note,)).route is Route.AUTO


def test_invoice_not_compared_with_an_erp_goes_to_review():
    """Without master data a changed bank account cannot be seen."""
    note = finding(FindingType.MASTER_DATA_NOT_CHECKED, Severity.INFO)
    assert causes(run(findings=(note,), match=None)) == [Cause.NOT_COMPARED]


def test_order_found_by_content_only_goes_to_review_unless_allowed():
    by_content = Match(po_number="PO-1", po_basis=Basis.CONTENT, delivery_id="LS-1")
    allowed = DecisionSettings(min_field_confidence=0.9, auto_on_order_found_by_content=True)
    assert (causes(run(match=by_content)), run(match=by_content, settings=allowed).route) == (
        [Cause.ORDER_BY_CONTENT],
        Route.AUTO,
    )


def test_order_quoted_with_a_slip_and_confirmed_by_a_delivery_is_fine():
    slip = Match(po_number="PO-1", po_basis=Basis.SIMILAR_REFERENCE, delivery_id="LS-1")
    assert run(match=slip).route is Route.AUTO


def test_order_without_a_delivery_on_file_goes_to_review():
    """Three-way match: nothing is paid for that has not arrived."""
    undelivered = Match(po_number="PO-1", po_basis=Basis.EXACT_REFERENCE)
    assert causes(run(match=undelivered)) == [Cause.NOT_DELIVERED]


def test_invoice_without_an_order_where_none_is_required_is_fine():
    assert run(match=Match(supplier_id="S1")).route is Route.AUTO


def test_invoice_a_reviewer_corrected_waits_for_approval():
    assert causes(run(corrected=True)) == [Cause.CORRECTED]


def test_every_reason_is_given_not_only_the_first():
    draft = invoice(number=None, field_confidence={"due_date": 0.2})
    decision = run(
        draft,
        status=DocumentStatus.EXTRACTED,
        findings=(finding(FindingType.IBAN_CHANGED),),
        corrected=True,
    )
    assert causes(decision) == [
        Cause.MISSING_FIELDS,
        Cause.LOW_CONFIDENCE,
        Cause.FINDINGS,
        Cause.CORRECTED,
    ]


def test_reading_causes_judge_only_what_was_read():
    draft = invoice(field_confidence={"due_date": 0.2})
    assert [reason.cause for reason in reading_causes(draft, SETTINGS)] == [Cause.LOW_CONFIDENCE]


def test_every_reason_says_why_in_words():
    decision = run(invoice(number=None), status=DocumentStatus.EXTRACTED, corrected=True)
    assert all(reason.message for reason in decision.reasons)


def test_shipped_thresholds_load_and_name_their_version():
    settings = DecisionSettings.load()
    assert (0 < settings.min_field_confidence <= 1, bool(settings.version)) == (True, True)
