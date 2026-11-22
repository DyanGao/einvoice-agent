"""Tamper evidence (app/db/chain.py, app/verify.py): every history is a hash
chain starting at the original's SHA-256, and `einvoice verify` names every
change to an original or a history event, and every gap."""

import hashlib

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import sessionmaker

from app import db
from app.cli import main as cli
from app.db.chain import event_hash
from app.db.rls import tenant_transaction
from app.ingest import ingest
from app.rotation import rotate_tenant
from app.verify import chain_older, verify
from tests.conftest import REJECT, StubValidator
from tests.support import XML, inline_queue

EVENTS = db.AuditEventRow.__table__


def reviewed(session_factory) -> str:
    """Stored, rejected by the validator, corrected and approved: a history of
    several events from the system and from a person."""
    queue = inline_queue(session_factory, validator=StubValidator(REJECT))
    document_id = ingest(session_factory, queue, XML, "r.xml").document_id
    with session_factory.begin() as session:
        document = session.get(db.DocumentRow, document_id)
        db.correct_fields(session, document, {"buyer_reference": "04011000-1"}, "Anna")
        db.approve(session, document, "Anna")
    return document_id


def history(session_factory, document_id: str) -> list[db.AuditEventRow]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(db.AuditEventRow)
                .where(db.AuditEventRow.document_id == document_id)
                .order_by(db.AuditEventRow.seq)
            )
        )


def note(session, document_id: str, text: str) -> None:
    document = session.get(db.DocumentRow, document_id)
    document.audit_events.append(
        db.AuditEventRow(event="note", actor="Anna", detail={"note": text})
    )


def breaks(session_factory) -> list[tuple[str, int | None]]:
    return [(p.kind, p.seq) for p in verify(session_factory).problems]


def test_every_event_is_chained_from_the_original_on(session_factory):
    document_id = reviewed(session_factory)
    events = history(session_factory, document_id)
    assert len(events) >= 4
    with session_factory() as session:
        document = session.get(db.DocumentRow, document_id)
        sha, head = document.sha256, document.audit_head
    assert events[0].prev_hash == sha == hashlib.sha256(XML).hexdigest()
    for before, after in zip(events, events[1:], strict=False):
        assert after.prev_hash == before.hash
    assert head == events[-1].hash
    assert all(e.hash == event_hash(e.prev_hash, document_id, e) for e in events)


def test_an_untouched_installation_verifies(session_factory):
    reviewed(session_factory)
    report = verify(session_factory)
    assert (report.documents, report.problems, report.unchained) == (1, [], 0)
    assert report.events >= 4
    assert len(report.digest) == 64


def test_a_changed_event_is_named(session_factory):
    document_id = reviewed(session_factory)
    victim = history(session_factory, document_id)[2]
    with session_factory.begin() as session:
        session.execute(
            update(EVENTS).where(EVENTS.c.seq == victim.seq).values(detail={"forged": True})
        )
    assert breaks(session_factory) == [("event_changed", victim.seq)]


def test_an_event_changed_with_its_own_hash_breaks_the_chain_after_it(session_factory):
    document_id = reviewed(session_factory)
    events = history(session_factory, document_id)
    victim = events[1]
    with session_factory.begin() as session:
        row = session.get(db.AuditEventRow, victim.seq)
        row.actor = "Mallory"
        row.hash = event_hash(row.prev_hash, document_id, row)
    assert breaks(session_factory) == [("chain_broken", events[2].seq)]


def test_a_removed_event_breaks_the_chain(session_factory):
    document_id = reviewed(session_factory)
    events = history(session_factory, document_id)
    with session_factory.begin() as session:
        session.execute(delete(EVENTS).where(EVENTS.c.seq == events[1].seq))
    assert breaks(session_factory) == [("chain_broken", events[2].seq)]


def test_a_history_cut_short_at_its_end_is_named(session_factory):
    document_id = reviewed(session_factory)
    last = history(session_factory, document_id)[-1]
    with session_factory.begin() as session:
        session.execute(delete(EVENTS).where(EVENTS.c.seq == last.seq))
    assert breaks(session_factory) == [("history_cut", None)]


def test_an_event_added_past_the_chain_is_named(session_factory):
    document_id = reviewed(session_factory)
    with session_factory.begin() as session:
        session.execute(
            insert(EVENTS).values(
                document_id=document_id, event="approved", actor="Mallory", detail={}
            )
        )
    (kind, seq), *_ = breaks(session_factory)
    assert kind == "event_unchained"
    assert seq > history(session_factory, document_id)[-2].seq


def test_a_changed_original_is_named(session_factory):
    document_id = reviewed(session_factory)
    with session_factory.begin() as session:
        session.get(db.DocumentRow, document_id).content = XML.replace(b"336.9", b"3369.0")
    assert breaks(session_factory) == [("original_changed", None)]


def test_an_original_whose_sealed_bytes_were_changed_is_named(session_factory):
    document_id = reviewed(session_factory)
    documents = db.DocumentRow.__table__
    with session_factory.begin() as session:
        sealed = session.scalar(select(documents.c.content).where(documents.c.id == document_id))
        session.execute(
            update(documents)
            .where(documents.c.id == document_id)
            .values(content=sealed[:-1] + bytes([sealed[-1] ^ 1]))
        )
    assert breaks(session_factory) == [("original_unreadable", None)]


def test_sealing_again_under_a_new_key_leaves_the_chain_whole(session_factory):
    reviewed(session_factory)
    rotate_tenant(session_factory, "default", apply=True)
    assert breaks(session_factory) == []


def test_a_second_writer_with_an_old_view_continues_the_chain(session_factory):
    document_id = reviewed(session_factory)
    first, second = session_factory(), session_factory()
    try:
        # Both hold the document as it was; the first adds an event and commits.
        mine, theirs = (
            first.get(db.DocumentRow, document_id),
            second.get(db.DocumentRow, document_id),
        )
        assert mine.audit_head == theirs.audit_head
        mine.audit_events.append(db.AuditEventRow(event="note", actor="first", detail={}))
        first.commit()
        # The second's view of the newest hash is old now; the chain goes on anyway.
        theirs.audit_events.append(db.AuditEventRow(event="note", actor="second", detail={}))
        second.commit()
    finally:
        first.close()
        second.close()
    assert breaks(session_factory) == []
    assert [e.actor for e in history(session_factory, document_id)[-2:]] == ["first", "second"]


def test_events_made_together_keep_their_order(session_factory):
    document_id = reviewed(session_factory)
    with session_factory.begin() as session:
        document = session.get(db.DocumentRow, document_id)
        for n in range(5):
            document.audit_events.append(db.AuditEventRow(event="note", actor=f"a{n}", detail={}))
    assert [e.actor for e in history(session_factory, document_id)[-5:]] == [
        f"a{n}" for n in range(5)
    ]
    assert breaks(session_factory) == []


def _from_before_the_chain(session_factory, document_id: str) -> None:
    documents = db.DocumentRow.__table__
    with session_factory.begin() as session:
        session.execute(update(EVENTS).values(prev_hash=None, hash=None))
        session.execute(
            update(documents).where(documents.c.id == document_id).values(audit_head=None)
        )


def test_events_from_before_the_chain_are_counted_and_then_chained(session_factory):
    document_id = reviewed(session_factory)
    _from_before_the_chain(session_factory, document_id)
    report = verify(session_factory)
    assert (report.problems, report.unchained) == ([], report.events)

    assert chain_older(session_factory, apply=False).events == report.events
    assert history(session_factory, document_id)[0].hash is None
    done = chain_older(session_factory, apply=True)
    assert (done.documents, done.events, done.refused) == (1, report.events, [])
    assert verify(session_factory).unchained == 0
    # New events go on from there.
    with session_factory.begin() as session:
        note(session, document_id, "one more look")
    assert breaks(session_factory) == []


def test_a_new_event_after_older_unchained_ones_starts_the_chain(session_factory):
    document_id = reviewed(session_factory)
    _from_before_the_chain(session_factory, document_id)
    with session_factory.begin() as session:
        note(session, document_id, "a look")
    report = verify(session_factory)
    assert report.problems == []
    assert report.unchained == report.events - 1
    chain_older(session_factory, apply=True)
    assert verify(session_factory).unchained == 0
    assert breaks(session_factory) == []


def test_a_broken_history_is_not_chained_over(session_factory):
    document_id = reviewed(session_factory)
    events = history(session_factory, document_id)
    with session_factory.begin() as session:
        session.execute(
            update(EVENTS).where(EVENTS.c.seq == events[0].seq).values(prev_hash=None, hash=None)
        )
        session.execute(update(EVENTS).where(EVENTS.c.seq == events[2].seq).values(actor="M"))
    done = chain_older(session_factory, apply=True)
    assert done.documents == 0
    # The changed event, and the chain that no longer starts where it should.
    assert {p.kind for p in done.refused} == {"event_changed", "chain_broken"}
    assert history(session_factory, document_id)[0].hash is None  # left as it was


def test_verify_and_chain_on_the_command_line(session_factory, capsys):
    document_id = reviewed(session_factory)
    assert cli(["verify"], session_factory=session_factory) == 0
    out = capsys.readouterr().out
    assert "1 documents, " in out
    assert "0 breaks; 0 events from before the chain" in out
    assert "digest " in out
    last = history(session_factory, document_id)[-1]
    with session_factory.begin() as session:
        session.execute(delete(EVENTS).where(EVENTS.c.seq == last.seq))
    assert cli(["verify"], session_factory=session_factory) == 1
    assert f"default {document_id}: history_cut" in capsys.readouterr().out
    assert cli(["chain"], session_factory=session_factory) == 0


@pytest.fixture
def two_tenants(session_factory):
    with session_factory.begin() as session:
        session.add(db.TenantRow(id="b", slug="b", name="B"))
    return session_factory


def test_each_tenants_histories_are_verified_acting_for_it(two_tenants):
    session_factory: sessionmaker = two_tenants
    reviewed(session_factory)
    queue = inline_queue(session_factory)
    ingest(session_factory, queue, XML, "b.xml", tenant_id="b")
    with tenant_transaction(session_factory, "b") as session:
        assert session.scalar(
            select(db.DocumentRow.audit_head).where(db.DocumentRow.tenant_id == "b")
        )
    report = verify(session_factory)
    assert (report.documents, report.problems) == (2, [])
