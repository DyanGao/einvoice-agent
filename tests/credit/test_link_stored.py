"""A credit note is linked to its invoice when it is checked, the link is
stored, and what earlier credit notes took back is counted (v1.0 Stage 5,
increment 2). Through ingestion, the checks and the API, with the ERP of the
matching world."""

import pytest
from fastapi.testclient import TestClient

from app import db
from app.api.main import create_app
from app.checks import FindingType
from app.checks.service import Checker
from app.db.review import reject
from app.ingest import ingest
from eval.generators.canonical import XRECHNUNG, credit_note_for
from eval.generators.world import PAID_ONCE, PO_EXACT, build_world
from eval.generators.xrechnung import render_cii, render_ubl
from eval.match import erp_for
from tests.support import inline_queue, settings_for


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture
def entry(world):
    return next(
        e for e in world.invoices if e.po_ref_kind == PO_EXACT and e.payment_kind == PAID_ONCE
    )


@pytest.fixture
def checker(world):
    return Checker(erp_for(world))


@pytest.fixture
def send(session_factory, checker):
    queue = inline_queue(session_factory, checker=checker)

    def send(xml: bytes, name: str) -> str:
        return ingest(session_factory, queue, xml, name).document_id

    return send


def _document(session_factory, document_id):
    session = session_factory()
    return session, session.get(db.DocumentRow, document_id)


def _types(document) -> set[str]:
    return {finding.type for finding in document.findings}


def test_a_credit_note_is_linked_to_the_invoice_it_names(session_factory, send, entry):
    invoice_id = send(render_ubl(entry.invoice, XRECHNUNG), "rechnung.xml")
    credit_id = send(render_cii(credit_note_for(entry.invoice, "GS-1"), XRECHNUNG), "gs1.xml")
    session, credit = _document(session_factory, credit_id)
    with session:
        assert credit.credit_link.invoice_document_id == invoice_id
        assert credit.credit_link.basis == "exact_reference"
        assert credit.credit_link.explanation_text["code"] == "credit.reference"
        # Still a credit note for a person; nothing else is wrong with it.
        assert _types(credit) == {FindingType.CREDIT_NOTE.value}
        # No order, delivery or payment of its own; the match says which invoice.
        assert (credit.match.po_number, credit.match.delivery_id, credit.match.payments) == (
            None,
            None,
            [],
        )
        assert credit.match.explanation_texts[0]["code"] == "credit.reference"


def test_a_second_full_credit_is_more_than_is_left_of_the_invoice(session_factory, send, entry):
    send(render_ubl(entry.invoice, XRECHNUNG), "rechnung.xml")
    send(render_ubl(credit_note_for(entry.invoice, "GS-1"), XRECHNUNG), "gs1.xml")
    second = send(render_ubl(credit_note_for(entry.invoice, "GS-2", 20), XRECHNUNG), "gs2.xml")
    session, credit = _document(session_factory, second)
    with session:
        exceeds = [f for f in credit.findings if f.type == FindingType.CREDIT_EXCEEDS_INVOICE]
        assert len(exceeds) == 1
        assert exceeds[0].text["params"]["open_amount"] == "0.00"


def test_a_rejected_credit_note_takes_nothing_back(session_factory, send, entry, checker):
    send(render_ubl(entry.invoice, XRECHNUNG), "rechnung.xml")
    first = send(render_ubl(credit_note_for(entry.invoice, "GS-1"), XRECHNUNG), "gs1.xml")
    second = send(render_ubl(credit_note_for(entry.invoice, "GS-2", 20), XRECHNUNG), "gs2.xml")
    with session_factory.begin() as session:
        reject(session, session.get(db.DocumentRow, first), "anna", "duplicate")
    with session_factory.begin() as session:
        checker.check(session, session.get(db.DocumentRow, second))
    session, credit = _document(session_factory, second)
    with session:
        assert FindingType.CREDIT_EXCEEDS_INVOICE.value not in _types(credit)


def test_a_credit_note_that_came_first_is_linked_once_its_invoice_is_there(
    session_factory, send, entry, checker
):
    credit_id = send(render_ubl(credit_note_for(entry.invoice, "GS-1"), XRECHNUNG), "gs1.xml")
    session, credit = _document(session_factory, credit_id)
    with session:
        assert credit.credit_link.invoice_document_id is None
        assert FindingType.CREDIT_NOT_LINKED.value in _types(credit)
    invoice_id = send(render_ubl(entry.invoice, XRECHNUNG), "rechnung.xml")
    with session_factory.begin() as session:
        checker.check(session, session.get(db.DocumentRow, credit_id))
    session, credit = _document(session_factory, credit_id)
    with session:
        assert credit.credit_link.invoice_document_id == invoice_id
        assert FindingType.CREDIT_NOT_LINKED.value not in _types(credit)


def test_the_api_shows_both_ends_of_the_link(session_factory, send, entry, checker, tmp_path):
    invoice_id = send(render_ubl(entry.invoice, XRECHNUNG), "rechnung.xml")
    credit_id = send(render_ubl(credit_note_for(entry.invoice, "GS-1"), XRECHNUNG), "gs1.xml")
    client = TestClient(
        create_app(session_factory, inline_queue(session_factory), settings_for(tmp_path), checker)
    )
    credit = client.get(f"/documents/{credit_id}").json()["credit"]
    assert credit["basis"] == "exact_reference"
    assert credit["invoice"]["document_id"] == invoice_id
    assert credit["invoice"]["number"] == entry.invoice.number
    assert credit["invoice"]["credited_by_others"] == "0"
    assert credit["invoice"]["left"] == f"{entry.invoice.gross_total:.2f}"
    invoice = client.get(f"/documents/{invoice_id}").json()
    assert invoice["credit"] is None
    assert [c["number"] for c in invoice["credits"]] == ["GS-1"]


def test_an_invoice_corrected_to_a_credit_note_and_back_loses_its_link(
    session_factory, send, entry, checker
):
    credit_id = send(render_ubl(credit_note_for(entry.invoice, "GS-1"), XRECHNUNG), "gs1.xml")
    with session_factory.begin() as session:
        document = session.get(db.DocumentRow, credit_id)
        document.draft.data = {**document.draft.data, "type_code": "380"}
        checker.check(session, document)
    session, document = _document(session_factory, credit_id)
    with session:
        assert document.credit_link is None
