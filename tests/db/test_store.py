from decimal import Decimal

from sqlalchemy import func, select

from app import db
from app.models import Invoice
from app.pipeline import process_structured
from tests.conftest import StubValidator
from tests.parse import samples

XML = samples.ubl("01.01a")


def store(session_factory, validator, data=XML, filename="rechnung.xml") -> str:
    with session_factory.begin() as session:
        outcome = process_structured(data, validator)
        return db.store_outcome(session, data, filename, outcome, db.DEFAULT_TENANT_ID).id


def events(session_factory, document_id) -> list[str]:
    with session_factory() as session:
        return [e.event for e in session.get(db.DocumentRow, document_id).audit_events]


def test_valid_document_is_stored_with_status_and_content(session_factory, accepting):
    document_id = store(session_factory, accepting)
    with session_factory() as session:
        row = session.get(db.DocumentRow, document_id)
        assert (row.status, row.format, row.content) == ("valid", "ubl", XML)


def test_stored_invoice_round_trips_to_the_same_model(session_factory, accepting):
    document_id = store(session_factory, accepting)
    with session_factory() as session:
        stored = Invoice.model_validate(session.get(db.DocumentRow, document_id).invoice.data)
    assert stored == process_structured(XML, accepting).invoice


def test_gross_total_column_is_exact(session_factory, accepting):
    document_id = store(session_factory, accepting)
    with session_factory() as session:
        assert session.get(db.DocumentRow, document_id).invoice.gross_total == Decimal("336.9")


def test_violations_are_stored(session_factory, rejecting):
    document_id = store(session_factory, rejecting)
    with session_factory() as session:
        validation = session.get(db.DocumentRow, document_id).validation
        assert (validation.valid, validation.violations[0]["rule_id"]) == (False, "BR-DE-15")


def test_audit_trail_lists_every_step_in_order(session_factory, accepting):
    document_id = store(session_factory, accepting)
    assert events(session_factory, document_id) == [
        "received",
        "format_detected",
        "validated",
        "parsed",
        "status_set",
    ]


def test_validator_failure_is_stored_without_a_validation_row(session_factory):
    document_id = store(session_factory, StubValidator(error="down"))
    with session_factory() as session:
        row = session.get(db.DocumentRow, document_id)
        assert (row.status, row.validation, row.error) == ("validation_failed", None, "down")


def test_same_bytes_are_found_again(session_factory, accepting):
    document_id = store(session_factory, accepting)
    with session_factory() as session:
        assert db.find_document(session, XML, db.DEFAULT_TENANT_ID).id == document_id


def test_duplicate_arrival_is_audited_without_a_second_document(session_factory, accepting):
    document_id = store(session_factory, accepting)
    with session_factory.begin() as session:
        db.record_duplicate(
            session, db.find_document(session, XML, db.DEFAULT_TENANT_ID), "again.xml"
        )
    with session_factory() as session:
        count = session.scalar(select(func.count()).select_from(db.DocumentRow))
    assert (count, events(session_factory, document_id)[-1]) == (1, "received_again")
