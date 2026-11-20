"""Tenants, increment 1: the application filter (v1.0 Stage 2).

Two companies in one installation: `default` and `acme`. Nothing of one is
visible to, or changeable by, the other. Row-level security in Postgres comes
in increment 2; these tests hold the application's own filter to account.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app import db
from app.api.main import create_app
from app.auth import (
    Role,
    add_membership,
    authenticate_token,
    create_token,
    create_user,
    sign_in,
)
from app.checks.service import Checker
from app.db.duplicates import redundant_copies
from app.delivery import deliver_due
from app.ingest import ingest
from app.rematch import open_invoices
from eval.generators.canonical import XRECHNUNG
from eval.generators.world import PAID_ONCE, PO_EXACT, UNPAID, build_world
from eval.generators.xrechnung import render_cii, render_ubl
from eval.match import erp_for
from tests.support import PDF, inline_queue, settings_for

ACME = "acme"
PASSWORD = "a-long-test-password"


@pytest.fixture
def tenants(session_factory):
    with session_factory.begin() as session:
        session.add(db.TenantRow(id=ACME, slug="acme", name="Acme GmbH"))


def bearer(session_factory, email, tenant_id, role=Role.REVIEWER) -> dict:
    with session_factory.begin() as session:
        user = create_user(session, email, email.split("@")[0].title(), role, tenant_id=tenant_id)
        return {"Authorization": f"Bearer {create_token(session, user.id, 'test', tenant_id)}"}


@pytest.fixture
def client(session_factory, tmp_path, tenants):
    settings = replace(settings_for(tmp_path), auth="on")
    return TestClient(create_app(session_factory, inline_queue(session_factory), settings))


@pytest.fixture
def anna(session_factory, tenants):
    return bearer(session_factory, "anna@default.example", db.DEFAULT_TENANT_ID)


@pytest.fixture
def bob(session_factory, tenants):
    return bearer(session_factory, "bob@acme.example", ACME)


def upload(client, headers, data=PDF, name="r.pdf"):
    response = client.post("/documents", files={"file": (name, data)}, headers=headers)
    assert response.status_code in (200, 202), response.text
    return response.json()


def test_the_default_tenant_exists_in_every_new_database(session_factory):
    with session_factory() as session:
        assert session.get(db.TenantRow, db.DEFAULT_TENANT_ID).slug == "default"


def test_the_same_file_sent_to_two_companies_is_two_documents(client, anna, bob):
    first, second = upload(client, anna), upload(client, bob)
    assert first["id"] != second["id"]
    assert (first["duplicate"], second["duplicate"]) == (False, False)
    # ... while within one company it is still the same document.
    assert upload(client, bob)["duplicate"] is True


def test_each_company_lists_only_its_own_documents(client, anna, bob):
    mine, theirs = upload(client, anna)["id"], upload(client, bob)["id"]
    listed = [row["id"] for row in client.get("/documents", headers=anna).json()["documents"]]
    assert mine in listed
    assert theirs not in listed


def test_every_document_route_answers_404_for_another_companys_document(client, anna, bob):
    theirs = upload(client, bob)["id"]
    checked = 0
    for route in client.app.routes:
        if "{document_id}" not in getattr(route, "path", ""):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            response = client.request(
                method,
                route.path.replace("{document_id}", theirs),
                headers=anna,
                json={"reason": "duplicate", "note": "x", "changes": {"number": "X"}},
            )
            assert response.status_code == 404, (method, route.path, response.status_code)
            checked += 1
    assert checked >= 7
    # And it is unchanged.
    assert client.get(f"/documents/{theirs}", headers=bob).json()["review_state"] == "pending"


def test_bulk_reject_skips_another_companys_document(client, anna, bob):
    theirs = upload(client, bob)["id"]
    response = client.post("/review/duplicates/reject", headers=anna, json={"ids": [theirs]})
    assert response.json() == {"rejected": [], "skipped": [theirs]}


def test_a_person_works_in_one_company_at_a_time_with_the_role_they_have_there(
    session_factory, tenants
):
    with session_factory.begin() as session:
        user = create_user(session, "eva@example.com", "Eva", Role.REVIEWER, password=PASSWORD)
        add_membership(session, user.id, ACME, Role.VIEWER)
        token = create_token(session, user.id, "acme script", ACME)
        user_id = user.id
    with session_factory.begin() as session:
        signed_in = sign_in(session, "eva@example.com", PASSWORD)
        by_token = authenticate_token(session, token, datetime.now(UTC))
    assert (signed_in.tenant_id, signed_in.role) == (db.DEFAULT_TENANT_ID, Role.REVIEWER)
    assert (by_token.tenant_id, by_token.role) == (ACME, Role.VIEWER)
    assert by_token.user_id == user_id


def test_a_token_stops_working_when_its_membership_is_taken_away(session_factory, tenants):
    with session_factory.begin() as session:
        user = create_user(session, "eva@example.com", "Eva", Role.REVIEWER, tenant_id=ACME)
        token = create_token(session, user.id, "script", ACME)
        session.delete(session.get(db.MembershipRow, (user.id, ACME)))
    with session_factory.begin() as session:
        assert authenticate_token(session, token, datetime.now(UTC)) is None


def test_no_token_for_a_company_the_user_is_not_in(session_factory, tenants):
    with session_factory.begin() as session:
        user = create_user(session, "eva@example.com", "Eva", Role.ADMIN)
        with pytest.raises(ValueError, match="not a member"):
            create_token(session, user.id, "script", ACME)


@pytest.fixture(scope="module")
def entry():
    world = build_world()
    return world, next(
        e for e in world.invoices if e.po_ref_kind == PO_EXACT and e.payment_kind == PAID_ONCE
    )


def test_an_invoice_another_company_received_is_not_a_duplicate(session_factory, tenants, entry):
    world, invoice = entry
    checker = Checker(erp_for(world))
    queue = inline_queue(session_factory, checker=checker)
    ingest(session_factory, queue, render_ubl(invoice.invoice, XRECHNUNG), "a.xml")
    theirs = ingest(
        session_factory,
        queue,
        render_cii(invoice.invoice, XRECHNUNG),
        "b.xml",
        tenant_id=ACME,
    ).document_id
    with session_factory.begin() as session:
        document = session.get(db.DocumentRow, theirs)
        # Checked as if Acme had the same ERP: still nothing on file to copy.
        checker.check(session, document)
        assert [finding.type for finding in document.findings] == []
        assert redundant_copies(session, ACME) == []


def test_another_companys_documents_are_not_checked_against_the_default_erp(
    session_factory, tenants, entry
):
    world, invoice = entry
    queue = inline_queue(session_factory, checker=Checker(erp_for(world)))
    theirs = ingest(
        session_factory, queue, render_ubl(invoice.invoice, XRECHNUNG), "b.xml", tenant_id=ACME
    ).document_id
    with session_factory() as session:
        document = session.get(db.DocumentRow, theirs)
        assert (document.match, document.decision, document.review_state) == (
            None,
            None,
            "pending",
        )


def test_payment_matching_sees_only_the_companys_open_invoices(session_factory, tenants):
    world = build_world()
    unpaid = next(
        e for e in world.invoices if e.po_ref_kind == PO_EXACT and e.payment_kind == UNPAID
    )
    checker = Checker(erp_for(world))
    queue = inline_queue(session_factory, checker=checker)
    ours = ingest(
        session_factory, queue, render_ubl(unpaid.invoice, XRECHNUNG), "a.xml"
    ).document_id
    with session_factory() as session:
        supplier_id = session.get(db.DocumentRow, ours).match.supplier_id
        # Open for its own company, invisible to the other.
        assert [d.id for d in open_invoices(session, db.DEFAULT_TENANT_ID, supplier_id)] == [ours]
        assert open_invoices(session, ACME, supplier_id) == []


class Recorder:
    name = "recorder"

    def __init__(self) -> None:
        self.delivered: list[str] = []

    def deliver(self, booking, key):
        self.delivered.append(booking.number)
        return "ref"


def test_the_default_connectors_never_get_another_companys_invoice(session_factory, tenants, entry):
    world, invoice = entry
    checker = Checker(erp_for(world))
    queue = inline_queue(session_factory, checker=checker)
    xml = render_ubl(invoice.invoice, XRECHNUNG)
    ours = ingest(session_factory, queue, xml, "a.xml").document_id
    theirs = ingest(session_factory, queue, xml, "b.xml", tenant_id=ACME).document_id
    with session_factory.begin() as session:
        # Matched as if Acme had the same ERP, so both could be booked.
        checker.check(session, session.get(db.DocumentRow, theirs))
        for document_id in (ours, theirs):
            document = session.get(db.DocumentRow, document_id)
            if document.review_state == "pending":
                db.approve(session, document, "anna")
    recorder = Recorder()
    done = deliver_due(session_factory, [recorder], datetime.now(UTC))
    assert done == [(ours, "recorder", "delivered")]
    # Acme's was deliverable all along: only the tenant kept it out.
    for_acme = deliver_due(session_factory, [Recorder()], datetime.now(UTC), tenant_id=ACME)
    assert for_acme == [(theirs, "recorder", "delivered")]
