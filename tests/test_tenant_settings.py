"""Each tenant's own settings (v1.0 Stage 2, increment 4): its ERP, its
connectors, its page limit — and secrets only ever named, never stored."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app import db
from app.checks.service import Checker
from app.cli import main
from app.connectors.csv_export import CsvExportConnector
from app.deliver import deliver_once
from app.erp import ErpUnavailable
from app.extract import BaselineExtractor
from app.ingest import ingest
from app.jobs import run_document_job
from app.tenants import (
    TenantConfig,
    TenantServices,
    config_for,
    create_tenant,
    parse_assignments,
    update_config,
)
from app.watch import watch_once
from eval.generators.canonical import XRECHNUNG
from eval.generators.world import PAID_ONCE, PO_EXACT, build_world
from eval.generators.xrechnung import render_ubl
from eval.match import erp_for
from tests.conftest import ACCEPT, StubValidator
from tests.pipeline.test_document import pages_of
from tests.support import PDF, FakeErp, RecordingQueue, settings_for

ERP_A, ERP_B = "http://erp-a.example", "http://erp-b.example"


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def entry(world):
    return next(
        e for e in world.invoices if e.po_ref_kind == PO_EXACT and e.payment_kind == PAID_ONCE
    )


@pytest.fixture
def settings(tmp_path):
    return replace(settings_for(tmp_path), erp_url=None, export_dir=None, webhook_url=None)


@pytest.fixture
def acme(session_factory):
    with session_factory.begin() as session:
        return create_tenant(session, "acme", "Acme GmbH").id


def configure(session_factory, tenant_id, settings, **changes):
    with session_factory.begin() as session:
        return update_config(session, tenant_id, changes, settings)


def test_the_default_tenant_without_settings_of_its_own_uses_env(session_factory, settings):
    env = replace(settings, erp_url=ERP_A, max_pages=7)
    with session_factory() as session:
        config = config_for(session, db.DEFAULT_TENANT_ID, env)
    assert (config.erp_url, config.max_pages) == (ERP_A, 7)


def test_settings_of_its_own_win_over_env(session_factory, settings):
    env = replace(settings, erp_url=ERP_A)
    configure(session_factory, db.DEFAULT_TENANT_ID, env, erp_url=ERP_B)
    with session_factory() as session:
        assert config_for(session, db.DEFAULT_TENANT_ID, env).erp_url == ERP_B


def test_a_new_tenant_starts_with_nothing(session_factory, settings, acme):
    with session_factory() as session:
        assert config_for(session, acme, replace(settings, erp_url=ERP_A)) == TenantConfig()


@pytest.mark.parametrize(
    ("assignment", "message"),
    [
        ("colour=blue", "no setting 'colour'"),
        ("erp_url", "expected key=value"),
        ("erp_url=ftp://erp.example", "not an http"),
        ("max_pages=0", "greater than or equal to 1"),
        ("erp_channel=fax", "'api' or 'form'"),
        ("webhook_secret_env=ANTHROPIC_API_KEY", "TENANT_"),
    ],
)
def test_settings_that_cannot_work_are_refused(
    session_factory, settings, acme, assignment, message
):
    with session_factory.begin() as session, pytest.raises(ValueError, match=message):
        update_config(session, acme, parse_assignments([assignment]), settings)


def test_an_empty_value_goes_back_to_the_default(session_factory, settings, acme):
    configure(session_factory, acme, settings, max_pages=3)
    with session_factory.begin() as session:
        config = update_config(session, acme, parse_assignments(["max_pages="]), settings)
    assert config.max_pages == TenantConfig().max_pages


def test_the_cli_adds_a_tenant_and_shows_its_settings_without_any_secret(
    session_factory, capsys, monkeypatch
):
    monkeypatch.setenv("TENANT_ACME_WEBHOOK_SECRET", "a-long-webhook-secret-for-acme-1234")
    run = lambda *argv: main(list(argv), session_factory=session_factory)  # noqa: E731
    assert run("tenant-add", "acme", "--name", "Acme GmbH") == 0
    assert run("tenant-add", "acme", "--name", "Again") == 1
    assert (
        run(
            "tenant-set",
            "acme",
            "webhook_url=https://hooks.acme.example/einvoice",
            "webhook_secret_env=TENANT_ACME_WEBHOOK_SECRET",
            "max_pages=4",
        )
        == 0
    )
    capsys.readouterr()
    assert run("tenant-show", "acme") == 0
    shown = capsys.readouterr().out
    assert json.loads(shown)["max_pages"] == 4
    assert "a-long-webhook-secret" not in shown
    with session_factory() as session:
        tenant_id = db.tenant_by_slug(session, "acme").id
        stored = json.dumps(session.get(db.TenantSettingsRow, tenant_id).config)
    assert "a-long-webhook-secret" not in stored


def test_a_bad_slug_is_refused(session_factory):
    with session_factory.begin() as session, pytest.raises(ValueError, match="slug"):
        create_tenant(session, "Acme GmbH", "Acme")


def services(session_factory, settings, world, **kwargs) -> TenantServices:
    """ERP_A is the world's ERP, in memory; ERP_B knows nobody."""
    erps = {ERP_A: erp_for(world), ERP_B: FakeErp()}

    def checker_factory(url, timeout):
        return Checker(erps[url]) if url else Checker()

    return TenantServices(session_factory, settings, checker_factory=checker_factory, **kwargs)


def process(session_factory, data, tenant_id, tenants, extractor=None) -> str:
    document_id = ingest(
        session_factory, RecordingQueue(), data, "r", tenant_id=tenant_id
    ).document_id
    run_document_job(
        session_factory, document_id, StubValidator(ACCEPT), extractor, tenants=tenants
    )
    return document_id


def test_each_tenant_is_checked_against_its_own_erp(session_factory, settings, acme, world, entry):
    configure(session_factory, db.DEFAULT_TENANT_ID, settings, erp_url=ERP_A)
    configure(session_factory, acme, settings, erp_url=ERP_B)
    tenants = services(session_factory, settings, world)
    xml = render_ubl(entry.invoice, XRECHNUNG)
    ours = process(session_factory, xml, db.DEFAULT_TENANT_ID, tenants)
    theirs = process(session_factory, xml, acme, tenants)
    with session_factory() as session:
        ours_row, theirs_row = (
            session.get(db.DocumentRow, ours),
            session.get(db.DocumentRow, theirs),
        )
        assert ours_row.match.supplier_id == entry.supplier_id
        # Acme's ERP does not know this supplier: its own ERP was asked.
        assert theirs_row.match.supplier_id is None
        assert "unknown_supplier" in [finding.type for finding in theirs_row.findings]


def test_each_tenant_has_its_own_page_limit(session_factory, settings, acme, world):
    configure(session_factory, acme, settings, max_pages=1)
    tenants = services(session_factory, settings, world)
    long_pdf = pages_of(PDF, 2)
    ours = process(session_factory, long_pdf, db.DEFAULT_TENANT_ID, tenants, BaselineExtractor())
    theirs = process(session_factory, long_pdf, acme, tenants, BaselineExtractor())
    with session_factory() as session:
        errors = [session.get(db.DocumentRow, d).error for d in (ours, theirs)]
    # The default tenant (limit 10) read it; Acme (limit 1) did not.
    assert errors[0] != errors[1]
    assert errors[1] == "2 pages, more than the 1 read automatically"


def test_each_tenant_books_into_its_own_systems(
    session_factory, settings, acme, world, entry, tmp_path
):
    ours_dir, theirs_dir = tmp_path / "default", tmp_path / "acme"
    configure(
        session_factory, db.DEFAULT_TENANT_ID, settings, erp_url=ERP_A, export_dir=str(ours_dir)
    )
    configure(session_factory, acme, settings, erp_url=ERP_A, export_dir=str(theirs_dir))
    tenants = services(
        session_factory,
        settings,
        world,
        connector_factory=lambda config, _: [CsvExportConnector(Path(config.export_dir))],
    )
    xml = render_ubl(entry.invoice, XRECHNUNG)
    documents = [process(session_factory, xml, t, tenants) for t in (db.DEFAULT_TENANT_ID, acme)]
    with session_factory.begin() as session:
        for document_id in documents:
            document = session.get(db.DocumentRow, document_id)
            if document.review_state == "pending":
                db.approve(session, document, "anna")
    done = deliver_once(session_factory, tenants, datetime.now(UTC))
    assert sorted(outcome for _, _, outcome in done) == ["delivered", "delivered"]
    assert len(list(ours_dir.glob("*.csv"))) == 1
    assert len(list(theirs_dir.glob("*.csv"))) == 1


def test_one_tenants_broken_settings_do_not_stop_the_others(
    session_factory, settings, acme, monkeypatch
):
    monkeypatch.delenv("TENANT_ACME_WEBHOOK_SECRET", raising=False)
    configure(
        session_factory,
        acme,
        settings,
        webhook_url="https://hooks.acme.example/einvoice",
        webhook_secret_env="TENANT_ACME_WEBHOOK_SECRET",
    )
    tenants = TenantServices(session_factory, settings)
    # The secret is not in the environment: no connectors for Acme, and a log line.
    assert tenants.connectors(acme) == []


class Bank:
    def __init__(self, down=False):
        self.down = down
        self.asked = 0

    def transactions_since(self, since: date | None):
        self.asked += 1
        if self.down:
            raise ErpUnavailable("connection refused")
        return []

    def supplier_with_iban(self, iban):  # pragma: no cover - no transactions
        return None


def test_the_bank_watch_asks_each_tenants_erp_and_one_outage_keeps_no_one_waiting(
    session_factory, settings, acme, world
):
    configure(session_factory, db.DEFAULT_TENANT_ID, settings, erp_url=ERP_A)
    configure(session_factory, acme, settings, erp_url=ERP_B)
    down, up = Bank(down=True), Bank()
    erps = {f"{ERP_A} 5.0": down, f"{ERP_B} 5.0": up}
    acted = watch_once(session_factory, services(session_factory, settings, world), erps)
    assert (down.asked, up.asked) == (1, 1)
    assert acted == {acme: []}


def test_a_tenant_without_an_erp_is_not_watched(session_factory, settings, acme, world):
    configure(session_factory, acme, settings, erp_url=ERP_B)
    erps = {f"{ERP_B} 5.0": Bank()}
    acted = watch_once(session_factory, services(session_factory, settings, world), erps)
    assert list(acted) == [acme]
