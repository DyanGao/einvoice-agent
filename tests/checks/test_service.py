"""Matching and the checks as the application runs them: on stored
documents, with the ERP and the other invoices on file."""

from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import db
from app.api.main import create_app
from app.checks.service import Checker
from app.ingest import ingest
from eval.generators.canonical import XRECHNUNG
from eval.generators.world import (
    PAID_COMBINED,
    PAID_ONCE,
    PO_EXACT,
    PO_MISSING,
    PO_TYPO,
    build_world,
)
from eval.generators.xrechnung import render_cii, render_ubl
from eval.match import erp_for
from tests.support import PDF, FakeErp, inline_queue, settings_for

REVIEWER = {"X-Reviewer": "anna"}
OTHER_IBAN = "DE02120300000000202051"


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def entry(world):
    """An invoice of the world that names its order correctly and was paid
    with one transfer that quotes its number."""
    return next(
        entry
        for entry in world.invoices
        if entry.po_ref_kind == PO_EXACT
        and entry.payment_kind == PAID_ONCE
        and entry.payment_ref_kind == "exact"
    )


@pytest.fixture(scope="module")
def erp(world):
    """The real ERP client against the mock ERP holding the world, in memory."""
    return erp_for(world)


def erp_knowing(world, entry, ibans) -> object:
    """The same, but with other bank accounts on file for the entry's supplier."""
    suppliers = tuple(
        replace(supplier, ibans=ibans) if supplier.id == entry.supplier_id else supplier
        for supplier in world.suppliers
    )
    return erp_for(replace(world, suppliers=suppliers))


def process(session_factory, data, checker) -> str:
    queue = inline_queue(session_factory, checker=checker)
    return ingest(session_factory, queue, data, "rechnung").document_id


def read(session_factory, document_id, get):
    with session_factory() as session:
        return get(session.get(db.DocumentRow, document_id))


def finding_types(session_factory, document_id) -> list[str]:
    return read(session_factory, document_id, lambda d: [f.type for f in d.findings])


def test_clean_invoice_from_a_known_supplier_on_a_known_order_needs_no_review(
    session_factory, erp, entry
):
    xml = render_ubl(entry.invoice, XRECHNUNG)
    document_id = process(session_factory, xml, Checker(erp))
    assert read(session_factory, document_id, lambda d: (d.findings, d.review_state)) == (
        [],
        "not_required",
    )


def test_valid_e_invoice_with_a_changed_bank_account_goes_to_review(session_factory, world, entry):
    xml = render_ubl(entry.invoice, XRECHNUNG)
    checker = Checker(erp_knowing(world, entry, (OTHER_IBAN,)))
    document_id = process(session_factory, xml, checker)
    assert read(session_factory, document_id, lambda d: (d.status, d.review_state)) == (
        "valid",
        "pending",
    )
    assert finding_types(session_factory, document_id) == ["iban_changed"]


def test_supplier_the_erp_does_not_know_goes_to_review(session_factory, world, entry):
    document_id = process(session_factory, render_ubl(entry.invoice, XRECHNUNG), Checker(FakeErp()))
    assert "unknown_supplier" in finding_types(session_factory, document_id)
    assert read(session_factory, document_id, lambda d: d.review_state) == "pending"


def test_same_invoice_arriving_as_another_file_is_a_duplicate_of_the_first(
    session_factory, erp, entry
):
    checker = Checker(erp)
    first = process(session_factory, render_ubl(entry.invoice, XRECHNUNG), checker)
    second = process(session_factory, render_cii(entry.invoice, XRECHNUNG), checker)
    duplicate = read(
        session_factory,
        second,
        lambda d: [(f.type, f.detail["document_id"]) for f in d.findings],
    )
    assert duplicate == [("duplicate", first)]
    assert finding_types(session_factory, first) == []


def test_without_an_erp_the_invoice_is_processed_and_carries_a_note(session_factory, world, entry):
    document_id = process(session_factory, render_ubl(entry.invoice, XRECHNUNG), Checker())
    findings = read(
        session_factory, document_id, lambda d: [(f.type, f.severity) for f in d.findings]
    )
    assert findings == [("master_data_not_checked", "info")]


def test_without_an_erp_even_a_valid_e_invoice_goes_to_a_person(session_factory, world, entry):
    """Nobody has confirmed the bank account: it is not booked unseen."""
    document_id = process(session_factory, render_ubl(entry.invoice, XRECHNUNG), Checker())
    assert read(
        session_factory, document_id, lambda d: (d.review_state, d.decision.reasons[0]["cause"])
    ) == ("pending", "not_compared")


def test_erp_outage_does_not_fail_the_document_but_sends_it_to_review(
    session_factory, world, entry
):
    document_id = process(
        session_factory, render_ubl(entry.invoice, XRECHNUNG), Checker(FakeErp(down=True))
    )
    state = read(session_factory, document_id, lambda d: (d.status, d.review_state))
    assert state == ("valid", "pending")
    assert finding_types(session_factory, document_id) == ["master_data_not_checked"]


def test_checks_are_recorded_in_the_audit_trail(session_factory, world, entry):
    checker = Checker(erp_knowing(world, entry, (OTHER_IBAN,)))
    document_id = process(session_factory, render_ubl(entry.invoice, XRECHNUNG), checker)
    event = read(session_factory, document_id, lambda d: d.audit_events[-1])
    assert (event.event, event.detail) == (
        "checked",
        {
            "findings": ["iban_changed"],
            "route": "review",
            "reasons": ["findings"],
            "thresholds": checker.decisions.version,
            "matched_order": entry.po_number,
            # The transfer went to the account the ERP no longer has on file.
            "matched_payments": [],
        },
    )


def test_extracted_pdf_is_checked_too(session_factory):
    document_id = process(session_factory, PDF, Checker(FakeErp()))
    assert "unknown_supplier" in finding_types(session_factory, document_id)


def test_document_nothing_was_read_from_has_no_findings(session_factory):
    document_id = process(session_factory, b"not an invoice", Checker(FakeErp()))
    assert finding_types(session_factory, document_id) == []


# --- matching ---------------------------------------------------------------


def stored_match(session_factory, document_id):
    return read(
        session_factory,
        document_id,
        lambda d: (
            None
            if d.match is None
            else (d.match.po_number, d.match.po_basis, d.match.delivery_id, d.match.payments)
        ),
    )


def test_match_is_stored_with_order_delivery_and_payment(session_factory, erp, entry):
    document_id = process(session_factory, render_ubl(entry.invoice, XRECHNUNG), Checker(erp))
    assert stored_match(session_factory, document_id) == (
        entry.po_number,
        "exact_reference",
        entry.delivery_id,
        [
            {"bank_transaction_id": p.bank_transaction_id, "amount": str(p.amount)}
            for p in entry.payments
        ],
    )


def test_mistyped_order_reference_is_matched_and_noted_instead_of_reported_missing(
    session_factory, erp, world
):
    typo = next(entry for entry in world.invoices if entry.po_ref_kind == PO_TYPO)
    document_id = process(session_factory, render_ubl(typo.invoice, XRECHNUNG), Checker(erp))
    assert stored_match(session_factory, document_id)[:2] == (typo.po_number, "similar_reference")
    findings = read(
        session_factory, document_id, lambda d: [(f.type, f.severity) for f in d.findings]
    )
    assert findings == [("po_not_quoted", "info")]
    assert read(session_factory, document_id, lambda d: d.review_state) == "not_required"


def test_invoice_without_an_order_reference_is_matched_by_what_it_bills(
    session_factory, erp, world
):
    silent = next(entry for entry in world.invoices if entry.po_ref_kind == PO_MISSING)
    document_id = process(session_factory, render_ubl(silent.invoice, XRECHNUNG), Checker(erp))
    assert stored_match(session_factory, document_id)[:2] == (silent.po_number, "content")
    assert finding_types(session_factory, document_id) == ["po_not_quoted"]


def test_order_found_only_by_what_it_bills_is_confirmed_by_a_person(session_factory, erp, world):
    silent = next(entry for entry in world.invoices if entry.po_ref_kind == PO_MISSING)
    document_id = process(session_factory, render_ubl(silent.invoice, XRECHNUNG), Checker(erp))
    assert read(
        session_factory, document_id, lambda d: (d.review_state, d.decision.reasons[0]["cause"])
    ) == ("pending", "order_by_content")


def test_decision_is_stored_with_its_reasons_and_replaced_on_every_run(
    session_factory, tmp_path, world, entry
):
    checker = Checker(erp_knowing(world, entry, (OTHER_IBAN,)))
    queue = inline_queue(session_factory, checker=checker)
    client = TestClient(create_app(session_factory, queue, settings_for(tmp_path), checker))
    document_id = uploaded(client, render_ubl(entry.invoice, XRECHNUNG))
    before = client.get(f"/documents/{document_id}").json()["decision"]
    (reason,) = before["reasons"]
    assert (before["route"], reason["cause"], reason["findings"]) == (
        "review",
        "findings",
        ["iban_changed"],
    )
    after = client.post(f"/documents/{document_id}/recheck").json()["decision"]
    assert after == before
    assert read(session_factory, document_id, lambda d: d.decision is not None) is True


def test_price_above_the_matched_order_is_found_although_the_order_is_not_quoted(
    session_factory, erp, world
):
    silent = next(entry for entry in world.invoices if entry.po_ref_kind == PO_MISSING)
    first, *rest = silent.invoice.items
    dearer = replace(
        silent.invoice,
        items=(replace(first, unit_price=first.unit_price * 2), *rest),
    )
    document_id = process(session_factory, render_ubl(dearer, XRECHNUNG), Checker(erp))
    assert "price_deviation" in finding_types(session_factory, document_id)


def test_without_an_erp_nothing_is_matched(session_factory, entry):
    document_id = process(session_factory, render_ubl(entry.invoice, XRECHNUNG), Checker())
    assert stored_match(session_factory, document_id) is None


def test_unknown_supplier_has_a_match_that_says_why_it_is_empty(session_factory, entry):
    document_id = process(session_factory, render_ubl(entry.invoice, XRECHNUNG), Checker(FakeErp()))
    explanation = read(session_factory, document_id, lambda d: d.match.explanation)
    assert "not on file" in explanation[0]


def test_correcting_the_order_reference_matches_again(client, world):
    typo = next(entry for entry in world.invoices if entry.po_ref_kind == PO_TYPO)
    document_id = uploaded(client, render_ubl(typo.invoice, XRECHNUNG))
    assert client.get(f"/documents/{document_id}").json()["match"]["po_basis"] == (
        "similar_reference"
    )
    fixed = correct(client, document_id, {"po_ref": typo.po_number})
    assert (fixed["match"]["po_basis"], fixed["findings"]) == ("exact_reference", [])


def test_combined_transfer_is_found_once_the_other_invoices_are_on_file(client, world):
    """One transfer for three invoices can only be told apart from any other
    large transfer when the other two invoices are known."""
    group = [e for e in world.invoices if e.payment_kind == PAID_COMBINED]
    first = next(e for e in group if e.payment_ref_kind == "exact")
    others = [
        e
        for e in group
        if e is not first
        and e.payments[0].bank_transaction_id == first.payments[0].bank_transaction_id
    ]
    document_id = uploaded(client, render_ubl(first.invoice, XRECHNUNG))
    assert client.get(f"/documents/{document_id}").json()["match"]["payments"] == []

    for other in others:
        uploaded(client, render_ubl(other.invoice, XRECHNUNG))
    again = client.post(f"/documents/{document_id}/recheck").json()
    assert again["match"]["payments"] == [
        {
            "bank_transaction_id": first.payments[0].bank_transaction_id,
            "amount": str(first.invoice.gross_total),
        }
    ]


def test_recheck_is_refused_for_an_approved_document(client, entry):
    document_id = uploaded(client, render_ubl(entry.invoice, XRECHNUNG))
    client.post(f"/documents/{document_id}/approve", headers=REVIEWER)
    assert client.post(f"/documents/{document_id}/recheck").status_code == 409


def test_recheck_is_refused_when_nothing_was_read(client):
    document_id = uploaded(client, b"not an invoice")
    assert client.post(f"/documents/{document_id}/recheck").status_code == 409


def test_recheck_without_configured_checks_says_so(session_factory, tmp_path, entry):
    bare = TestClient(
        create_app(session_factory, inline_queue(session_factory), settings_for(tmp_path))
    )
    document_id = uploaded(bare, render_ubl(entry.invoice, XRECHNUNG))
    response = bare.post(f"/documents/{document_id}/recheck")
    assert (response.status_code, "no checks" in response.json()["detail"]) == (409, True)


# --- through the API --------------------------------------------------------


@pytest.fixture
def client(session_factory, tmp_path, erp):
    checker = Checker(erp)
    queue = inline_queue(session_factory, checker=checker)
    return TestClient(create_app(session_factory, queue, settings_for(tmp_path), checker))


def uploaded(client, data) -> str:
    return client.post("/documents", files={"file": ("rechnung.xml", data)}).json()["id"]


def correct(client, document_id, changes) -> dict:
    response = client.patch(
        f"/documents/{document_id}/fields", json={"changes": changes}, headers=REVIEWER
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_detail_and_list_show_the_findings(session_factory, tmp_path, world, entry):
    checker = Checker(erp_knowing(world, entry, (OTHER_IBAN,)))
    queue = inline_queue(session_factory, checker=checker)
    client = TestClient(create_app(session_factory, queue, settings_for(tmp_path), checker))
    document_id = uploaded(client, render_ubl(entry.invoice, XRECHNUNG))
    (finding,) = client.get(f"/documents/{document_id}").json()["findings"]
    assert (finding["type"], finding["severity"], finding["fields"]) == (
        "iban_changed",
        "review",
        ["payment_ibans"],
    )
    (summary,) = client.get("/documents").json()["documents"]
    assert summary["findings_to_review"] == 1


def test_correcting_a_field_runs_the_checks_again(client, entry):
    document_id = uploaded(client, render_ubl(entry.invoice, XRECHNUNG))
    wrong = correct(client, document_id, {"gross_total": "1.00"})
    assert [f["type"] for f in wrong["findings"]] == ["totals_mismatch"]
    assert wrong["review_state"] == "pending"

    right = correct(client, document_id, {"gross_total": str(entry.invoice.gross_total)})
    assert (right["findings"], right["review_state"]) == ([], "not_required")


def test_correcting_a_finding_away_does_not_end_the_review(session_factory, tmp_path, world, entry):
    checker = Checker(erp_knowing(world, entry, (OTHER_IBAN,)))
    queue = inline_queue(session_factory, checker=checker)
    client = TestClient(create_app(session_factory, queue, settings_for(tmp_path), checker))
    document_id = uploaded(client, render_ubl(entry.invoice, XRECHNUNG))
    # Typing in the account the ERP knows makes the finding go away ...
    changed = correct(client, document_id, {"payment_ibans": [OTHER_IBAN]})
    # ... but the invoice no longer says what the supplier sent: a person approves it.
    assert (changed["findings"], changed["review_state"]) == ([], "pending")


def test_findings_of_an_approved_document_stay_as_the_reviewer_saw_them(
    session_factory, client, entry
):
    document_id = uploaded(client, render_ubl(entry.invoice, XRECHNUNG))
    correct(client, document_id, {"po_ref": None})
    client.post(f"/documents/{document_id}/approve", headers=REVIEWER)
    with session_factory.begin() as session:
        document = session.get(db.DocumentRow, document_id)
        assert Checker(FakeErp()).check(session, document) == []
        # Without the reference the order was still found by content.
        assert [f.type for f in document.findings] == ["po_not_quoted"]


def test_delivery_billed_again_under_a_new_number_and_date_goes_to_review(
    session_factory, erp, entry
):
    """Neither number nor date gives it away; the delivery it bills does."""
    checker = Checker(erp)
    first = process(session_factory, render_ubl(entry.invoice, XRECHNUNG), checker)
    again = replace(
        entry.invoice,
        number="K-70001234",
        issue_date=entry.invoice.issue_date + timedelta(days=9),
        due_date=entry.invoice.due_date + timedelta(days=9),
    )
    second = process(session_factory, render_ubl(again, XRECHNUNG), checker)
    assert read(
        session_factory,
        second,
        lambda d: ([(f.type, f.detail["document_id"]) for f in d.findings], d.review_state),
    ) == ([("delivery_already_billed", first)], "pending")


def test_checking_again_leaves_a_rejected_document_rejected(session_factory, erp, entry):
    xml = render_ubl(entry.invoice, XRECHNUNG)
    checker = Checker(erp)
    document_id = process(session_factory, xml, checker)
    with session_factory.begin() as session:
        document = session.get(db.DocumentRow, document_id)
        document.review_state = "pending"
        db.reject(session, document, "anna", "not_addressed_to_us")
    with session_factory.begin() as session:
        assert checker.check(session, session.get(db.DocumentRow, document_id)) == []
    assert read(session_factory, document_id, lambda d: d.review_state) == "rejected"
