"""One bank watch and one delivery loop per database (hardening H2)."""

import os

import pytest
from sqlalchemy import create_engine

from app.db.instance_lock import AlreadyRunning, InstanceLock, LockLost

on_postgres = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="advisory locks need Postgres (make test-pg)",
)


@pytest.fixture
def engine(session_factory):
    return session_factory.kw["bind"]


@on_postgres
def test_second_instance_is_refused_while_the_first_runs(engine):
    first = InstanceLock(engine, "bank-watch").acquire()
    try:
        with pytest.raises(AlreadyRunning, match="bank-watch"):
            InstanceLock(engine, "bank-watch").acquire()
    finally:
        first.release()


@on_postgres
def test_second_instance_starts_once_the_first_stopped(engine):
    InstanceLock(engine, "bank-watch").acquire().release()
    InstanceLock(engine, "bank-watch").acquire().release()


@on_postgres
def test_watch_and_delivery_do_not_block_each_other(engine):
    watch = InstanceLock(engine, "bank-watch").acquire()
    try:
        InstanceLock(engine, "delivery").acquire().release()
    finally:
        watch.release()


@on_postgres
def test_a_process_on_another_engine_is_refused_too(engine):
    """Another process has its own engine and connection pool."""
    first = InstanceLock(engine, "delivery").acquire()
    other = create_engine(engine.url)
    try:
        with pytest.raises(AlreadyRunning):
            InstanceLock(other, "delivery").acquire()
    finally:
        first.release()
        other.dispose()


@on_postgres
def test_check_passes_while_the_lock_is_held(engine):
    lock = InstanceLock(engine, "bank-watch").acquire()
    try:
        lock.check()
    finally:
        lock.release()


@on_postgres
def test_check_fails_when_the_locks_connection_is_gone(engine):
    """After a database restart the lock is gone: the loop must stop, not run
    on next to a second instance that may have taken it meanwhile."""
    lock = InstanceLock(engine, "bank-watch").acquire()
    lock._connection.invalidate()
    with pytest.raises(LockLost):
        lock.check()


def test_without_postgres_there_is_no_lock_and_no_error():
    engine = create_engine("sqlite://")
    lock = InstanceLock(engine, "bank-watch").acquire()
    lock.check()
    InstanceLock(engine, "bank-watch").acquire()  # not enforced here
    lock.release()
