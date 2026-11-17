import logging

import pytest

from app.db.engine import database_engine, uses_development_password


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://einvoice:einvoice@postgres:5432/einvoice",
        "postgresql+psycopg://einvoice:einvoice@db.example.com/einvoice",
    ],
)
def test_development_password_on_a_database_elsewhere_is_flagged(url):
    assert uses_development_password(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://einvoice:einvoice@127.0.0.1:5433/einvoice",
        "postgresql+psycopg://einvoice:einvoice@localhost/einvoice",
        "postgresql+psycopg://einvoice:s3cret-and-long@postgres:5432/einvoice",
        "sqlite://",
    ],
    ids=["loopback", "localhost", "own-password", "sqlite"],
)
def test_local_database_or_own_password_is_not_flagged(url):
    assert not uses_development_password(url)


def test_engine_warns_without_showing_the_password(caplog):
    with caplog.at_level(logging.WARNING, logger="app.db.engine"):
        database_engine("postgresql+psycopg://einvoice:einvoice@postgres:5432/einvoice")
    (record,) = caplog.records
    assert "POSTGRES_PASSWORD" in record.getMessage()
    assert ":einvoice@" not in record.getMessage()


def test_engine_on_a_local_database_says_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger="app.db.engine"):
        database_engine("sqlite://")
    assert caplog.records == []
