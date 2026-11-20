"""Background work per tenant (v1.0 Stage 2, increment 3): the worker, folder
watch, bank watch, delivery loop and the CLI act for one tenant at a time,
under row-level security on Postgres like a request."""

import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app import db
from app.auth import authenticate_token, sign_in
from app.checks.service import Checker
from app.cli import main
from app.db import rls
from app.delivery import deliver_due
from app.ingest import SETTLE_SECONDS, ingest, requeue_stuck
from app.watch import run_once
from eval.generators.canonical import XRECHNUNG
from eval.generators.world import PAID_ONCE, PO_EXACT, build_world
from eval.generators.xrechnung import render_cii, render_ubl
from eval.match import erp_for
from tests.support import PDF, XML, RecordingQueue, inline_queue

ACME = "acme"
ON_POSTGRES = bool(os.environ.get("TEST_DATABASE_URL"))
PASSWORD = "a-long-test-password"


@pytest.fixture
def tenants(session_factory):
    with session_factory.begin() as session:
        session.add(db.TenantRow(id=ACME, slug="acme", name="Acme GmbH"))


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def entry(world):
    return next(
        e for e in world.invoices if e.po_ref_kind == PO_EXACT and e.payment_kind == PAID_ONCE
    )


class SpyChecker(Checker):
    """A real checker that also notes as whom, and for which tenant, the
    transaction it is given runs."""

    def __init__(self, erp) -> None:
        super().__init__(erp)
        self.seen: list[tuple[str, str | None]] = []

    def _note(self, session) -> None:
        if session.get_bind().dialect.name == "postgresql":
            self.seen.append(
                tuple(
                    session.execute(
                        text("SELECT current_user, current_setting('app.tenant_id', true)")
                    ).one()
                )
            )

    def check(self, session, document, **kwargs):
        self._note(session)
        return super().check(session, document, **kwargs)

    def match_payments(self, session, document, **kwargs):
        self._note(session)
        return super().match_payments(session, document, **kwargs)


def tenant_of(session_factory, document_id) -> str:
    with session_factory() as session:
        return session.get(db.DocumentRow, document_id).tenant_id


def test_documents_of_two_tenants_processed_in_turn_stay_with_their_tenant(
    session_factory, tenants, world, entry
):
    queue = inline_queue(session_factory, checker=Checker(erp_for(world)))
    ours = [
        ingest(session_factory, queue, render_ubl(entry.invoice, XRECHNUNG), "a.xml"),
        ingest(session_factory, queue, PDF, "a.pdf"),
    ]
    theirs = [
        ingest(
            session_factory, queue, render_cii(entry.invoice, XRECHNUNG), "b.xml", tenant_id=ACME
        ),
        ingest(session_factory, queue, PDF, "b.pdf", tenant_id=ACME),
    ]
    with session_factory() as session:
        for item, tenant in [
            *((i, db.DEFAULT_TENANT_ID) for i in ours),
            *((i, ACME) for i in theirs),
        ]:
            document = session.get(db.DocumentRow, item.document_id)
            assert document.tenant_id == tenant
            assert document.status != "received"
            # Only the default tenant's documents are checked against its ERP.
            assert (document.decision is not None) == (tenant == db.DEFAULT_TENANT_ID)
            # The CII copy is not a duplicate of the default tenant's UBL.
            assert all(finding.type != "duplicate" for finding in document.findings)


@pytest.mark.skipif(not ON_POSTGRES, reason="row-level security needs Postgres")
def test_the_worker_checks_a_document_under_row_level_security(session_factory, world, entry):
    checker = SpyChecker(erp_for(world))
    queue = inline_queue(session_factory, checker=checker)
    ingest(session_factory, queue, render_ubl(entry.invoice, XRECHNUNG), "a.xml")
    assert checker.seen
    assert set(checker.seen) == {(rls.TENANT_ROLE, db.DEFAULT_TENANT_ID)}


@pytest.mark.skipif(not ON_POSTGRES, reason="row-level security needs Postgres")
def test_the_bank_watch_matches_payments_under_row_level_security(session_factory, world, entry):
    erp = erp_for(world)
    ingest(
        session_factory,
        inline_queue(session_factory, checker=Checker(erp)),
        render_ubl(entry.invoice, XRECHNUNG),
        "a.xml",
    )
    spy = SpyChecker(erp)
    run_once(session_factory, erp, spy)
    assert spy.seen
    assert set(spy.seen) == {(rls.TENANT_ROLE, db.DEFAULT_TENANT_ID)}


def test_delivery_works_tenant_by_tenant(session_factory, tenants, monkeypatch):
    acted_for = []
    real = rls.tenant_transaction

    def recording(factory, tenant_id):
        acted_for.append(tenant_id)
        return real(factory, tenant_id)

    monkeypatch.setattr("app.delivery.tenant_transaction", recording)

    class Nothing:
        name = "nothing"

        def deliver(self, booking, key):  # pragma: no cover - nothing is due
            raise AssertionError

    deliver_due(session_factory, [Nothing()], datetime.now(UTC), tenant_id=ACME)
    assert acted_for == [ACME]


def test_a_lost_job_of_another_tenant_is_queued_again_in_its_own_history(session_factory, tenants):
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    with session_factory.begin() as session:
        document = db.create_received(session, XML, "r.xml", ACME)
        document.created_at = now - timedelta(minutes=30)
        session.flush()
        document_id = document.id
    queue = RecordingQueue()
    requeue_stuck(
        session_factory,
        queue,
        now=now,
        older_than=timedelta(minutes=10),
        pending=lambda _: False,
    )
    with session_factory() as session:
        events = [e.event for e in session.get(db.DocumentRow, document_id).audit_events]
    assert queue.document_ids == [document_id]
    assert events[-1] == "queued_again"


def settled(path, data=XML):
    path.write_bytes(data)
    old = path.stat().st_mtime - 10 * SETTLE_SECONDS
    os.utime(path, (old, old))


def test_folder_watch_stores_for_the_tenant_it_is_told(capsys, tmp_path, session_factory, tenants):
    settled(tmp_path / "rechnung.xml")
    queue = RecordingQueue()
    code = main(
        ["watch", str(tmp_path), "--once", "--tenant", "acme"],
        session_factory=session_factory,
        queue=queue,
    )
    (line,) = capsys.readouterr().out.splitlines()
    assert code == 0
    assert tenant_of(session_factory, json.loads(line)["document_id"]) == ACME


def test_process_stores_for_the_tenant_it_is_told(
    capsys, tmp_path, session_factory, tenants, accepting
):
    path = tmp_path / "rechnung.xml"
    path.write_bytes(XML)
    main(
        ["process", str(path), "--tenant", "acme"],
        validator=accepting,
        session_factory=session_factory,
    )
    report = json.loads(capsys.readouterr().out.splitlines()[0])
    assert tenant_of(session_factory, report["document_id"]) == ACME


def test_an_unknown_tenant_is_refused(capsys, tmp_path, session_factory):
    code = main(
        ["watch", str(tmp_path), "--once", "--tenant", "nobody"],
        session_factory=session_factory,
        queue=RecordingQueue(),
    )
    assert (code, "no tenant 'nobody'" in capsys.readouterr().err) == (2, True)


def run(session_factory, *argv):
    return main(list(argv), session_factory=session_factory, password_prompt=lambda: PASSWORD)


def test_a_person_is_added_to_a_second_company_with_another_role(session_factory, tenants):
    base = ["user-add", "eva@example.com", "--name", "Eva"]
    assert run(session_factory, *base, "--role", "reviewer", "--password") == 0
    assert run(session_factory, *base, "--role", "viewer", "--tenant", "acme") == 0
    with session_factory() as session:
        roles = {m.tenant_id: m.role for m in session.query(db.MembershipRow).all()}
        assert sign_in(session, "eva@example.com", PASSWORD).tenant_id == db.DEFAULT_TENANT_ID
    assert roles == {db.DEFAULT_TENANT_ID: "reviewer", ACME: "viewer"}


def test_a_token_is_made_for_the_tenant_it_is_asked_for(capsys, session_factory, tenants):
    run(
        session_factory,
        "user-add",
        "bot@example.com",
        "--name",
        "Bot",
        "--role",
        "reviewer",
        "--tenant",
        "acme",
    )
    capsys.readouterr()
    assert (
        run(session_factory, "token-create", "bot@example.com", "--name", "s", "--tenant", "acme")
        == 0
    )
    token = capsys.readouterr().out.splitlines()[-1]
    with session_factory.begin() as session:
        assert authenticate_token(session, token, datetime.now(UTC)).tenant_id == ACME


def test_no_token_for_a_tenant_the_user_is_not_in(capsys, session_factory, tenants):
    run(session_factory, "user-add", "bot@example.com", "--name", "Bot", "--role", "reviewer")
    code = run(
        session_factory, "token-create", "bot@example.com", "--name", "s", "--tenant", "acme"
    )
    assert (code, "not a member" in capsys.readouterr().err) == (1, True)
