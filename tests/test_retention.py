"""Retention and legal hold (app/retention.py): nothing goes before its
retention end, nothing under a hold, nothing without the tenant's switch;
what goes leaves an entry in the append-only deletion log."""

import os
import random
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app import db
from app.cli import main as cli
from app.db.rls import tenant_transaction
from app.ingest import ingest
from app.retention import may_go, retention_end, run, set_hold
from app.settings import Settings
from app.tenants import TenantConfig, update_config
from app.verify import verify
from tests.support import PDF, XML, inline_queue

SETTINGS = Settings.from_env()
AT = datetime(2026, 11, 22, 10, tzinfo=UTC)


# --- the rule -------------------------------------------------------------


def test_kept_eight_years_after_the_end_of_the_latest_year():
    # Dated in 2026, received in 2026, booked in January 2027: the booking's year counts.
    end = retention_end(date(2026, 12, 30), AT, [datetime(2027, 1, 4, tzinfo=UTC)], 8)
    assert end == date(2035, 12, 31)


def test_an_invoice_received_years_after_its_date_counts_from_receipt():
    assert retention_end(date(2016, 4, 4), AT, [], 8) == date(2034, 12, 31)


def test_an_unread_document_counts_from_its_receipt():
    assert retention_end(None, AT, [], 8) == date(2034, 12, 31)


def test_it_may_go_from_the_first_of_january_after_its_end():
    end = date(2034, 12, 31)
    assert not may_go(end, date(2034, 12, 31))
    assert may_go(end, date(2035, 1, 1))


def test_a_leap_day_counts_like_any_day_of_its_year():
    assert retention_end(date(2028, 2, 29), datetime(2028, 3, 1, tzinfo=UTC), [], 8) == date(
        2036, 12, 31
    )


def test_fewer_than_eight_years_are_refused():
    with pytest.raises(ValueError, match="at least 8"):
        retention_end(date(2026, 1, 1), AT, [], 7)
    with pytest.raises(ValueError, match="greater than or equal to 8"):
        TenantConfig(retention_years=6)


def _random_day(rng: random.Random, first: date, last: date) -> date:
    return first + timedelta(days=rng.randrange((last - first).days + 1))


def test_nothing_may_go_before_its_retention_end_over_random_dates():
    """A property test (seeded, 20,000 cases): whatever the dates, a document
    may go exactly from 1 January after `years` full years past its latest
    year, never a day before."""
    rng = random.Random(20261122)  # noqa: S311 - reproducible test data
    for _ in range(20_000):
        issued = _random_day(rng, date(1990, 1, 1), date(2090, 12, 31))
        if rng.random() < 0.05:
            issued = rng.choice((date(2024, 2, 29), date(2028, 2, 29), date(2027, 12, 31)))
        received = issued + timedelta(days=rng.randrange(-30, 800))
        booked = [received + timedelta(days=rng.randrange(0, 400)) for _ in range(rng.randrange(3))]
        years = rng.randrange(8, 31)
        dated = None if rng.random() < 0.1 else issued  # an unread document has no date
        end = retention_end(
            dated,
            datetime(received.year, received.month, received.day, tzinfo=UTC),
            [datetime(b.year, b.month, b.day, tzinfo=UTC) for b in booked],
            years,
        )
        years_counted = [received.year, *(b.year for b in booked)]
        latest = max(years_counted + ([dated.year] if dated else []))
        first_day = date(latest + years + 1, 1, 1)
        assert end == first_day - timedelta(days=1)
        assert end.year - latest == years >= 8
        today = first_day + timedelta(days=rng.randrange(-800, 800))
        assert may_go(end, today) == (today >= first_day)
        # Never inside the years it must be kept, whichever year counted.
        for kept in ([dated] if dated else []) + [received, *booked]:
            if today <= date(kept.year + years, 12, 31):
                assert not may_go(end, today)


# --- on stored documents --------------------------------------------------


def stored(session_factory, data: bytes = XML, name: str = "r.xml", tenant_id: str = "default"):
    queue = inline_queue(session_factory)
    return ingest(session_factory, queue, data, name, tenant_id=tenant_id).document_id


def counts(session_factory, today: date, *, apply: bool):
    return run(session_factory, SETTINGS, today, apply=apply)


def allow_deleting(session_factory, tenant_id: str = "default", **more) -> None:
    with session_factory.begin() as session:
        update_config(session, tenant_id, {"deletion_enabled": True, **more}, SETTINGS)


AFTER = date(2035, 1, 1)  # every document stored today may go then
BEFORE = date(2034, 12, 31)


def test_nothing_is_deleted_before_its_end_even_with_deleting_on(session_factory):
    stored(session_factory)
    allow_deleting(session_factory)
    found = counts(session_factory, BEFORE, apply=True).tenants["default"]
    assert (found.kept, found.due, found.deleted, found.next_end) == (1, 0, 0, BEFORE)


def test_past_its_end_nothing_is_deleted_while_deleting_is_off(session_factory):
    document_id = stored(session_factory)
    found = counts(session_factory, AFTER, apply=True).tenants["default"]
    assert (found.due, found.deleted, found.deletion_off) == (1, 0, True)
    with session_factory() as session:
        assert session.get(db.DocumentRow, document_id) is not None


def test_a_document_past_its_end_is_deleted_and_logged(session_factory):
    document_id = stored(session_factory)
    with session_factory() as session:
        document = session.get(db.DocumentRow, document_id)
        sha, head, events = document.sha256, document.audit_head, len(document.audit_events)
    allow_deleting(session_factory)
    assert counts(session_factory, AFTER, apply=False).tenants["default"].deleted == 0

    found = counts(session_factory, AFTER, apply=True).tenants["default"]
    assert (found.due, found.deleted) == (1, 1)
    with session_factory() as session:
        assert session.get(db.DocumentRow, document_id) is None
        for table in ("drafts", "invoices", "audit_events", "validation_results"):
            left = session.scalar(
                text(f"SELECT COUNT(*) FROM {table} WHERE document_id = :id"),  # noqa: S608
                {"id": document_id},
            )
            assert left == 0, table
        (entry,) = session.scalars(select(db.DeletionLogRow))
    assert (entry.document_id, entry.sha256, entry.audit_head, entry.events) == (
        document_id,
        sha,
        head,
        events,
    )
    assert entry.retention_end == BEFORE
    assert entry.deleted_by == "retention"
    assert verify(session_factory).problems == []


def test_a_document_under_a_legal_hold_is_kept(session_factory):
    held, other = stored(session_factory), stored(session_factory, PDF, "r.pdf")
    with session_factory.begin() as session:
        set_hold(session, session.get(db.DocumentRow, held), on=True, reason="audit", actor="op")
    allow_deleting(session_factory)
    found = counts(session_factory, AFTER, apply=True).tenants["default"]
    assert (found.held, found.deleted) == (1, 1)
    with session_factory() as session:
        assert session.get(db.DocumentRow, held) is not None
        assert session.get(db.DocumentRow, other) is None


def test_a_tenants_legal_hold_keeps_all_of_it(session_factory):
    stored(session_factory)
    allow_deleting(session_factory, legal_hold=True)
    found = counts(session_factory, AFTER, apply=True).tenants["default"]
    assert (found.held, found.deleted) == (1, 0)


def test_a_hold_and_its_release_are_in_the_chained_history(session_factory):
    document_id = stored(session_factory)
    with session_factory.begin() as session:
        document = session.get(db.DocumentRow, document_id)
        set_hold(session, document, on=True, reason="Betriebsprüfung 2027", actor="op")
        with pytest.raises(ValueError, match="already"):
            set_hold(session, document, on=True, reason="again", actor="op")
        set_hold(session, document, on=False, reason="done", actor="op")
        with pytest.raises(ValueError, match="needs a reason"):
            set_hold(session, document, on=True, reason=" ", actor="op")
    with session_factory() as session:
        events = session.get(db.DocumentRow, document_id).audit_events
        assert [e.event for e in events[-2:]] == ["legal_hold", "legal_hold_released"]
        assert events[-2].detail == {"reason": "Betriebsprüfung 2027"}
    assert verify(session_factory).problems == []


def test_a_credit_note_outlives_the_invoice_it_corrects(session_factory):
    invoice_id, credit_id = stored(session_factory), stored(session_factory, PDF, "gs.pdf")
    with session_factory.begin() as session:
        session.add(
            db.CreditLinkRow(
                document_id=credit_id, invoice_document_id=invoice_id, basis="exact_reference",
                explanation="names the invoice",
            )
        )  # fmt: skip
        # The credit note is younger: received a year later.
        session.get(db.DocumentRow, credit_id).created_at = AT + timedelta(days=365)
    allow_deleting(session_factory)
    found = counts(session_factory, AFTER, apply=True).tenants["default"]
    assert (found.deleted, found.kept) == (1, 1)
    with session_factory() as session:
        link = session.get(db.DocumentRow, credit_id).credit_link
        assert link is not None
        assert link.invoice_document_id is None


def test_each_tenant_follows_its_own_settings(session_factory):
    with session_factory.begin() as session:
        session.add(db.TenantRow(id="b", slug="b", name="B"))
    stored(session_factory)
    stored(session_factory, tenant_id="b")
    allow_deleting(session_factory, tenant_id="b", retention_years=10)
    allow_deleting(session_factory)
    report = counts(session_factory, AFTER, apply=True)
    assert (report.tenants["default"].deleted, report.tenants["b"].deleted) == (1, 0)
    assert report.tenants["b"].next_end == date(2036, 12, 31)
    with tenant_transaction(session_factory, "b") as session:
        logged = select(func.count()).select_from(db.DeletionLogRow)
        assert session.scalar(logged.where(db.DeletionLogRow.tenant_id == "b")) == 0
        # Another tenant's entries: hidden by row-level security on Postgres.
        hidden = session.get_bind().dialect.name == "postgresql"
        others = session.scalar(logged.where(db.DeletionLogRow.tenant_id == "default"))
        assert others == (0 if hidden else 1)


def test_the_deletion_log_cannot_be_changed(session_factory):
    stored(session_factory)
    allow_deleting(session_factory)
    counts(session_factory, AFTER, apply=True)
    with session_factory() as session:
        entry = session.scalars(select(db.DeletionLogRow)).one()
        entry.deleted_by = "nobody"
        with pytest.raises(ValueError, match="append-only"):
            session.flush()
        session.rollback()
        session.delete(session.scalars(select(db.DeletionLogRow)).one())
        with pytest.raises(ValueError, match="append-only"):
            session.flush()


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="Postgres: the trigger")
@pytest.mark.parametrize(
    "statement", ["UPDATE deletion_log SET deleted_by = 'x'", "DELETE FROM deletion_log",
                  "TRUNCATE deletion_log"],
)  # fmt: skip
def test_postgres_refuses_every_change_to_the_log_for_every_role(session_factory, statement):
    stored(session_factory)
    allow_deleting(session_factory)
    counts(session_factory, AFTER, apply=True)
    with session_factory() as session, pytest.raises(DBAPIError, match="append-only"):
        session.execute(text(statement))


def test_retention_and_hold_on_the_command_line(session_factory, capsys):
    document_id = stored(session_factory)
    assert cli(["retention"], session_factory=session_factory) == 0
    assert "default: 1 documents, 1 within their retention" in capsys.readouterr().out
    assert cli(["hold", document_id, "--reason", "audit"], session_factory=session_factory) == 0
    assert f"{document_id}: legal hold on" in capsys.readouterr().out
    assert cli(["hold", document_id, "--reason", "x"], session_factory=session_factory) == 2
    assert cli(["hold", "nope", "--reason", "x"], session_factory=session_factory) == 2
    assert (
        cli(["hold", document_id, "--release", "--reason", "done"], session_factory=session_factory)
        == 0
    )
