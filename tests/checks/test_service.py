"""The checks as the application runs them: on stored documents, with the
ERP and the other invoices on file."""

import pytest
from fastapi.testclient import TestClient

from app import db
from app.api.main import create_app
from app.checks import PoLine, PurchaseOrder, Supplier
from app.checks.service import Checker
from app.ingest import ingest
from eval.generators.canonical import XRECHNUNG
from eval.generators.world import PO_EXACT, build_world
from eval.generators.xrechnung import render_cii, render_ubl
from tests.support import PDF, FakeErp, inline_queue, settings_for

REVIEWER = {"X-Reviewer": "anna"}
OTHER_IBAN = "DE02120300000000202051"


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def entry(world):
    """An invoice of the world that names its order correctly."""
    return next(entry for entry in world.invoices if entry.po_ref_kind == PO_EXACT)


def master_data(world, entry, ibans=None) -> tuple[Supplier, PurchaseOrder]:
    supplier = next(s for s in world.suppliers if s.id == entry.supplier_id)
    order = next(o for o in world.orders if o.number == entry.po_number)
    return (
        Supplier(
            supplier.id,
            supplier.company.name,
            supplier.company.vat_id,
            supplier.ibans if ibans is None else ibans,
        ),
        PurchaseOrder(
            order.number,
            tuple(PoLine(x.description, x.quantity, x.unit_price) for x in order.lines),
        ),
    )


def erp_knowing(world, entry, **options) -> FakeErp:
    supplier, order = master_data(world, entry, **options)
    return FakeErp([supplier], [order])


def process(session_factory, data, checker) -> str:
    queue = inline_queue(session_factory, checker=checker)
    return ingest(session_factory, queue, data, "rechnung").document_id


def read(session_factory, document_id, get):
    with session_factory() as session:
        return get(session.get(db.DocumentRow, document_id))


def finding_types(session_factory, document_id) -> list[str]:
    return read(session_factory, document_id, lambda d: [f.type for f in d.findings])


def test_clean_invoice_from_a_known_supplier_on_a_known_order_needs_no_review(
    session_factory, world, entry
):
    xml = render_ubl(entry.invoice, XRECHNUNG)
    document_id = process(session_factory, xml, Checker(erp_knowing(world, entry)))
    assert read(session_factory, document_id, lambda d: (d.findings, d.review_state)) == (
        [],
        "not_required",
    )


def test_valid_e_invoice_with_a_changed_bank_account_goes_to_review(session_factory, world, entry):
    xml = render_ubl(entry.invoice, XRECHNUNG)
    checker = Checker(erp_knowing(world, entry, ibans=(OTHER_IBAN,)))
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
    session_factory, world, entry
):
    checker = Checker(erp_knowing(world, entry))
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
    assert read(session_factory, document_id, lambda d: d.review_state) == "not_required"


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
    checker = Checker(erp_knowing(world, entry, ibans=(OTHER_IBAN,)))
    document_id = process(session_factory, render_ubl(entry.invoice, XRECHNUNG), checker)
    event = read(session_factory, document_id, lambda d: d.audit_events[-1])
    assert (event.event, event.detail) == (
        "checked",
        {"findings": ["iban_changed"], "needs_review": True},
    )


def test_extracted_pdf_is_checked_too(session_factory):
    document_id = process(session_factory, PDF, Checker(FakeErp()))
    assert "unknown_supplier" in finding_types(session_factory, document_id)


def test_document_nothing_was_read_from_has_no_findings(session_factory):
    document_id = process(session_factory, b"not an invoice", Checker(FakeErp()))
    assert finding_types(session_factory, document_id) == []


# --- through the API --------------------------------------------------------


@pytest.fixture
def client(session_factory, tmp_path, world, entry):
    checker = Checker(erp_knowing(world, entry))
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
    checker = Checker(erp_knowing(world, entry, ibans=(OTHER_IBAN,)))
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
    checker = Checker(erp_knowing(world, entry, ibans=(OTHER_IBAN,)))
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
        assert [f.type for f in document.findings] == ["missing_po"]
