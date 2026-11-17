from decimal import Decimal

import pytest

from app import db
from app.extract import ExtractionError
from app.ingest import ingest
from app.models import Invoice
from tests.conftest import REJECT, StubValidator
from tests.support import PDF, PDF_DOCUMENT, XML, inline_queue


class FailingExtractor:
    def extract(self, data):
        raise ExtractionError("model stopped with 'refusal'")


def processed(session_factory, data=PDF, **queue_options) -> str:
    return ingest(
        session_factory, inline_queue(session_factory, **queue_options), data, "f"
    ).document_id


def correct(session_factory, document_id, changes, actor="anna"):
    with session_factory.begin() as session:
        return db.correct_fields(session, session.get(db.DocumentRow, document_id), changes, actor)


def approve(session_factory, document_id, actor="anna"):
    with session_factory.begin() as session:
        return db.approve(session, session.get(db.DocumentRow, document_id), actor)


def load(session_factory, document_id, read):
    with session_factory() as session:
        return read(session.get(db.DocumentRow, document_id))


def test_extracted_pdf_waits_for_review(session_factory):
    document_id = processed(session_factory)
    assert load(session_factory, document_id, lambda d: (d.status, d.review_state)) == (
        "extracted",
        "pending",
    )


def test_accepted_structured_invoice_without_checks_waits_for_review(session_factory):
    """Without the checks nothing decided it may skip review, and nothing
    but a decision or a person's approval lets it be booked."""
    document_id = processed(session_factory, XML)
    assert load(session_factory, document_id, lambda d: d.review_state) == "pending"


def test_rejected_structured_invoice_waits_for_review(session_factory):
    document_id = processed(session_factory, XML, validator=StubValidator(REJECT))
    assert load(session_factory, document_id, lambda d: d.review_state) == "pending"


def test_correction_changes_the_draft(session_factory):
    document_id = processed(session_factory)
    correct(session_factory, document_id, {"number": "RE-KORRIGIERT"})
    assert load(session_factory, document_id, lambda d: d.draft.data["number"]) == "RE-KORRIGIERT"


def test_correction_is_audited_with_old_value_new_value_and_reviewer(session_factory):
    document_id = processed(session_factory)
    correct(session_factory, document_id, {"number": "RE-KORRIGIERT"}, actor="anna")
    event = load(session_factory, document_id, lambda d: d.audit_events[-1])
    assert (event.event, event.actor, event.detail) == (
        "field_corrected",
        "anna",
        {"field": "number", "old": PDF_DOCUMENT.invoice.number, "new": "RE-KORRIGIERT"},
    )


def test_machine_output_is_kept_unchanged_next_to_the_correction(session_factory):
    document_id = processed(session_factory)
    correct(session_factory, document_id, {"number": "RE-KORRIGIERT"})
    original = load(session_factory, document_id, lambda d: d.draft.original["number"])
    assert original == PDF_DOCUMENT.invoice.number


@pytest.mark.parametrize(
    ("path", "value", "read"),
    [
        ("seller.vat_id", "DE999999999", lambda data: data["seller"]["vat_id"]),
        ("lines.0.net", "12.50", lambda data: data["lines"][0]["net"]),
        ("payment_ibans", ["DE79000000001234567890"], lambda data: data["payment_ibans"]),
        ("due_date", "2026-12-24", lambda data: data["due_date"]),
        ("po_ref", None, lambda data: data["po_ref"]),
    ],
    ids=["nested", "line-item", "list", "date", "clear"],
)
def test_fields_are_addressed_by_dotted_path(session_factory, path, value, read):
    document_id = processed(session_factory)
    correct(session_factory, document_id, {path: value})
    assert load(session_factory, document_id, lambda d: read(d.draft.data)) == value


def test_setting_a_field_to_its_current_value_is_not_a_correction(session_factory):
    document_id = processed(session_factory)
    changed = correct(session_factory, document_id, {"number": PDF_DOCUMENT.invoice.number})
    events = load(session_factory, document_id, lambda d: [e.event for e in d.audit_events])
    assert (changed, "field_corrected" in events) == ([], False)


@pytest.mark.parametrize(
    "changes",
    [
        {"no_such_field": "x"},
        {"lines.99.net": "1.00"},
        {"seller.no_such": "x"},
        {"source_format": "ubl"},
        {"field_confidence": {}},
        {"": "x"},
    ],
    ids=["unknown", "line-out-of-range", "unknown-nested", "format", "confidence", "empty"],
)
def test_unknown_or_read_only_field_is_rejected(session_factory, changes):
    document_id = processed(session_factory)
    with pytest.raises(db.ReviewError):
        correct(session_factory, document_id, changes)


@pytest.mark.parametrize(
    "changes",
    [{"gross_total": 119.5}, {"gross_total": "viel"}, {"issue_date": "31.02.2026"}],
    ids=["float", "not-a-number", "impossible-date"],
)
def test_invalid_value_is_rejected(session_factory, changes):
    document_id = processed(session_factory)
    with pytest.raises(db.ReviewError, match="invalid value"):
        correct(session_factory, document_id, changes)


def test_one_invalid_value_rejects_the_whole_request(session_factory):
    document_id = processed(session_factory)
    with pytest.raises(db.ReviewError):
        correct(session_factory, document_id, {"number": "RE-NEU", "gross_total": "viel"})
    assert load(session_factory, document_id, lambda d: d.draft.data["number"]) == (
        PDF_DOCUMENT.invoice.number
    )


def test_approval_makes_the_draft_the_invoice_of_record(session_factory):
    document_id = processed(session_factory)
    correct(session_factory, document_id, {"number": "RE-KORRIGIERT"})
    approve(session_factory, document_id)
    stored = load(session_factory, document_id, lambda d: Invoice.model_validate(d.invoice.data))
    assert (stored.number, stored.gross_total) == (
        "RE-KORRIGIERT",
        PDF_DOCUMENT.invoice.gross_total,
    )


def test_approval_is_audited_with_the_reviewer(session_factory):
    document_id = processed(session_factory)
    approve(session_factory, document_id, actor="bernd")
    event, state = load(
        session_factory, document_id, lambda d: (d.audit_events[-1], d.review_state)
    )
    assert (event.event, event.actor, state) == ("approved", "bernd", "approved")


def test_incomplete_draft_cannot_be_approved_and_names_what_is_missing(session_factory):
    document_id = processed(session_factory)
    correct(session_factory, document_id, {"gross_total": None, "seller.name": None})
    with pytest.raises(db.ReviewError, match="gross_total.*seller.name"):
        approve(session_factory, document_id)
    assert load(session_factory, document_id, lambda d: d.review_state) == "pending"


def test_approved_document_cannot_be_changed_or_approved_again(session_factory):
    document_id = processed(session_factory)
    approve(session_factory, document_id)
    with pytest.raises(db.NotReviewable):
        correct(session_factory, document_id, {"number": "X"})
    with pytest.raises(db.NotReviewable):
        approve(session_factory, document_id)


def test_approving_a_corrected_structured_invoice_replaces_its_parsed_values(session_factory):
    document_id = processed(session_factory, XML, validator=StubValidator(REJECT))
    correct(session_factory, document_id, {"gross_total": "1.00"})
    approve(session_factory, document_id)
    assert load(session_factory, document_id, lambda d: d.invoice.gross_total) == Decimal("1.00")


def test_document_nothing_could_be_read_from_can_be_filled_in_by_hand(session_factory):
    document_id = processed(session_factory, extractor=FailingExtractor())
    correct(session_factory, document_id, {"number": "RE-VON-HAND"})
    draft = load(session_factory, document_id, lambda d: d.draft)
    assert (draft.data["number"], draft.original["number"]) == ("RE-VON-HAND", None)


def test_document_without_draft_cannot_be_approved(session_factory):
    document_id = processed(session_factory, extractor=FailingExtractor())
    with pytest.raises(db.NotReviewable, match="no draft"):
        approve(session_factory, document_id)


# --- rejecting: a person decides the document is not to be booked -----------


def reject(session_factory, document_id, reason="duplicate", note=None, actor="anna"):
    with session_factory.begin() as session:
        db.reject(session, session.get(db.DocumentRow, document_id), actor, reason, note)


def test_rejected_document_is_marked_and_audited_with_reason_and_reviewer(session_factory):
    document_id = processed(session_factory)
    reject(session_factory, document_id, "duplicate", "same as RE-1 from Monday")
    state, event = load(
        session_factory, document_id, lambda d: (d.review_state, d.audit_events[-1])
    )
    assert state == "rejected"
    assert (event.event, event.actor, event.detail) == (
        "rejected",
        "anna",
        {"reason": "duplicate", "note": "same as RE-1 from Monday"},
    )


def test_unknown_reason_is_refused(session_factory):
    document_id = processed(session_factory)
    with pytest.raises(db.ReviewError, match="reason"):
        reject(session_factory, document_id, "dont_like_it")


def test_other_reason_needs_a_note(session_factory):
    document_id = processed(session_factory)
    with pytest.raises(db.ReviewError, match="note"):
        reject(session_factory, document_id, "other", "  ")


def test_approved_document_cannot_be_rejected(session_factory):
    document_id = processed(session_factory, XML)
    approve(session_factory, document_id)
    with pytest.raises(db.NotReviewable, match="approved"):
        reject(session_factory, document_id)


def test_rejected_document_can_be_neither_corrected_nor_approved_nor_rejected_again(
    session_factory,
):
    document_id = processed(session_factory)
    reject(session_factory, document_id)
    for action in (
        lambda: correct(session_factory, document_id, {"number": "X"}),
        lambda: approve(session_factory, document_id),
        lambda: reject(session_factory, document_id),
    ):
        with pytest.raises(db.NotReviewable, match="rejected"):
            action()
