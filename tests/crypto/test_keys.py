"""Keys and sealed values (app/crypto.py): one data key per tenant, wrapped by
the master key; a sealed value opens only with its key, in its place."""

import base64
import os

import pytest
from sqlalchemy import create_engine, select

from app import crypto
from app.db import Base, TenantKeyRow, TenantRow
from app.db.tables import DEFAULT_TENANT_ID

MASTER = crypto.MasterKey(b"m" * 32)
OTHER_MASTER = crypto.MasterKey(b"o" * 32)


@pytest.fixture
def engine(tmp_path):
    """A database file, or the Postgres test database: the key store uses
    connections of its own."""
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'keys.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(TenantRow.__table__.insert().values(id="b", slug="b", name="B"))
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


def keyring(engine, master=MASTER) -> crypto.Keyring:
    return crypto.Keyring(master, crypto.DatabaseKeyStore(engine))


def test_a_sealed_value_opens_to_what_was_sealed(engine):
    ring = keyring(engine)
    sealed = ring.seal(DEFAULT_TENANT_ID, b"<Invoice>RE-1</Invoice>", "documents.content")
    assert crypto.is_sealed(sealed)
    assert b"RE-1" not in sealed
    assert ring.open(sealed, "documents.content") == b"<Invoice>RE-1</Invoice>"


def test_the_same_value_seals_differently_each_time(engine):
    ring = keyring(engine)
    one, two = (ring.seal(DEFAULT_TENANT_ID, b"same", "documents.content") for _ in range(2))
    assert one != two


def test_each_tenant_has_its_own_data_key(engine):
    ring = keyring(engine)
    assert ring.key_for(DEFAULT_TENANT_ID) != ring.key_for("b")
    with engine.connect() as connection:
        rows = connection.execute(select(TenantKeyRow.tenant_id, TenantKeyRow.version)).all()
    assert sorted(rows) == [("b", 1), (DEFAULT_TENANT_ID, 1)]


def test_the_stored_key_is_wrapped_not_plain(engine):
    ring = keyring(engine)
    sealed = ring.seal("b", b"x", "p")
    with engine.connect() as connection:
        wrapped = connection.scalar(select(TenantKeyRow.wrapped))
    # Another process with the same master key opens it from the stored key alone.
    assert keyring(engine).open(sealed, "p") == b"x"
    assert len(wrapped) == 12 + 32 + 16  # nonce, key, tag


def test_a_value_moved_to_another_place_does_not_open(engine):
    ring = keyring(engine)
    sealed = ring.seal(DEFAULT_TENANT_ID, b"x", "documents.content")
    with pytest.raises(crypto.SealError, match="drafts.data"):
        ring.open(sealed, "drafts.data")


def test_a_changed_byte_is_detected(engine):
    ring = keyring(engine)
    sealed = bytearray(ring.seal(DEFAULT_TENANT_ID, b"invoice", "p"))
    sealed[-1] ^= 1
    with pytest.raises(crypto.SealError):
        ring.open(bytes(sealed), "p")


def test_another_master_key_opens_nothing_and_says_why(engine):
    sealed = keyring(engine).seal(DEFAULT_TENANT_ID, b"x", "p")
    with pytest.raises(crypto.CryptoError, match="another master key"):
        keyring(engine, OTHER_MASTER).open(sealed, "p")


def test_a_wrapped_key_changed_in_the_database_does_not_open(engine):
    sealed = keyring(engine).seal(DEFAULT_TENANT_ID, b"x", "p")
    with engine.begin() as connection:
        wrapped = connection.scalar(select(TenantKeyRow.wrapped))
        connection.execute(
            TenantKeyRow.__table__.update().values(wrapped=wrapped[:-1] + bytes([wrapped[-1] ^ 1]))
        )
    with pytest.raises(crypto.CryptoError, match="does not open with this MASTER_KEY"):
        keyring(engine).open(sealed, "p")


def test_two_processes_making_the_first_key_end_up_with_one(engine):
    store = crypto.DatabaseKeyStore(engine)
    first = crypto.StoredKey("a" * 32, "b", 1, MASTER.wrap(b"k" * 32, "b", 1), MASTER.id)
    second = crypto.StoredKey("c" * 32, "b", 1, MASTER.wrap(b"l" * 32, "b", 1), MASTER.id)
    assert store.add(first) == first
    assert store.add(second) == first
    assert store.count() == 1


def test_a_value_stored_before_sealing_is_read_as_it_is(engine):
    assert keyring(engine).open(b"%PDF-1.7 plain", "documents.content") == b"%PDF-1.7 plain"


def test_the_master_key_is_32_bytes_of_base64():
    assert crypto.MasterKey.parse(crypto.new_master_key()).key != MASTER.key
    with pytest.raises(ValueError, match="32 bytes"):
        crypto.MasterKey.parse(base64.b64encode(b"short").decode())
    with pytest.raises(ValueError, match="base64"):
        crypto.MasterKey.parse("not base64!")


def test_the_master_key_never_shows_in_its_repr():
    assert "mmmm" not in repr(MASTER)
    assert repr(MASTER) == f"MasterKey(id={MASTER.id})"


def test_without_a_master_key_a_process_refuses_to_start(engine, monkeypatch):
    monkeypatch.delenv("MASTER_KEY", raising=False)
    monkeypatch.delenv("ENCRYPTION", raising=False)
    with pytest.raises(ValueError, match="MASTER_KEY is not set"):
        crypto.configure_from_env(engine)


def test_with_a_master_key_the_keyring_uses_the_database(engine, monkeypatch):
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(MASTER.key).decode())
    ring = crypto.configure_from_env(engine)
    assert ring.master_id == MASTER.id
    assert crypto.current() is ring


def test_encryption_off_stores_values_as_they_come(engine, monkeypatch):
    monkeypatch.setenv("ENCRYPTION", "off")
    assert crypto.configure_from_env(engine) is None
    assert crypto.seal(DEFAULT_TENANT_ID, b"plain", "p") == b"plain"


def test_encryption_must_be_on_or_off(engine, monkeypatch):
    monkeypatch.setenv("ENCRYPTION", "maybe")
    with pytest.raises(ValueError, match="on or off"):
        crypto.configure_from_env(engine)


def test_a_sealed_value_with_encryption_off_says_so(monkeypatch, keyring):
    sealed = keyring.seal(DEFAULT_TENANT_ID, b"x", "p")
    crypto.configure(None)
    with pytest.raises(crypto.CryptoError, match="encryption is off"):
        crypto.open_sealed(sealed, "p")


def test_a_process_that_never_configured_keys_stores_nothing(monkeypatch):
    monkeypatch.setattr(crypto, "_keyring", None)
    with pytest.raises(crypto.CryptoError, match="not configured"):
        crypto.seal(DEFAULT_TENANT_ID, b"x", "p")
