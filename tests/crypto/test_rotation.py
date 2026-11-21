"""Key rotation (app/rotation.py) and backups: a new master key wraps the data
keys anew without sealing anything again; a tenant's new data key seals its
data again; a backup opens with its key and with nothing else."""

import base64
import shutil

import pytest
from sqlalchemy import LargeBinary, create_engine, select, type_coerce
from sqlalchemy.orm import sessionmaker

from app import crypto, db
from app.cli import main as cli
from app.ingest import ingest
from app.rotation import rotate_master, rotate_tenant
from app.sealing import seal_documents, sealed_columns
from tests.support import XML, inline_queue

OLD = crypto.MasterKey(b"o" * 32)
NEW = crypto.MasterKey(b"n" * 32)


def database(path):
    engine = create_engine(f"sqlite:///{path}")
    db.Base.metadata.create_all(engine, checkfirst=True)
    return engine, sessionmaker(engine)


def use(engine, master, *previous) -> crypto.Keyring:
    keyring = crypto.Keyring(master, crypto.DatabaseKeyStore(engine), previous)
    crypto.configure(keyring)
    return keyring


def stored_invoice(session_factory) -> str:
    return ingest(session_factory, inline_queue(session_factory), XML, "r.xml").document_id


def read_all(session_factory, document_id: str) -> tuple:
    with session_factory() as session:
        document = session.get(db.DocumentRow, document_id)
        return document.content, document.filename, document.draft.data, document.invoice.data


def raw_values(session_factory) -> list[bytes]:
    with session_factory() as session:
        values = list(session.scalars(select(db.DocumentRow.sealed_content)))
        for table, name, _ in sealed_columns():
            query = select(type_coerce(table.c[name], LargeBinary)).where(
                table.c[name].is_not(None)
            )
            values += list(session.scalars(query))
    return values


@pytest.fixture
def stored(tmp_path):
    """A database with an invoice sealed under the old master key."""
    engine, session_factory = database(tmp_path / "einvoice.db")
    use(engine, OLD)
    document_id = stored_invoice(session_factory)
    yield engine, session_factory, document_id, read_all(session_factory, document_id)
    engine.dispose()


# --- a new master key -----------------------------------------------------


def test_with_the_old_key_as_previous_everything_still_opens(stored):
    engine, session_factory, document_id, before = stored
    use(engine, NEW, OLD)
    assert read_all(session_factory, document_id) == before


def test_rotating_the_master_key_wraps_the_data_keys_and_seals_nothing_again(stored):
    engine, session_factory, document_id, before = stored
    raw_before = raw_values(session_factory)
    use(engine, NEW, OLD)
    assert rotate_master(apply=False).rewrapped == 1
    assert crypto.DatabaseKeyStore(engine).all()[0].master_id == OLD.id  # only counted
    assert rotate_master(apply=True).rewrapped == 1
    assert raw_values(session_factory) == raw_before
    # The new key alone opens everything now; the old one alone nothing.
    use(engine, NEW)
    assert read_all(session_factory, document_id) == before
    use(engine, OLD)
    with pytest.raises(crypto.CryptoError, match="another master key"):
        read_all(session_factory, document_id)
    use(engine, NEW, OLD)
    assert rotate_master(apply=True).rewrapped == 0


def test_without_the_previous_key_rotation_says_what_is_missing(stored):
    engine, *_ = stored
    use(engine, NEW)
    with pytest.raises(crypto.CryptoError, match="MASTER_KEY_PREVIOUS"):
        rotate_master(apply=False)


def test_the_previous_keys_come_from_the_environment(stored, monkeypatch):
    engine, session_factory, document_id, before = stored
    monkeypatch.setenv("MASTER_KEY", base64.b64encode(NEW.key).decode())
    other = base64.b64encode(b"x" * 32).decode()
    monkeypatch.setenv("MASTER_KEY_PREVIOUS", f"{other}, {base64.b64encode(OLD.key).decode()}")
    crypto.configure_from_env(engine)
    assert read_all(session_factory, document_id) == before


def test_rotating_the_master_key_on_the_command_line(stored, capsys):
    engine, session_factory, *_ = stored
    use(engine, NEW, OLD)
    assert cli(["key-rotate-master"], session_factory=session_factory) == 0
    assert "data keys: 1 to wrap anew, 0 wrapped by MASTER_KEY already" in capsys.readouterr().out
    assert cli(["key-rotate-master", "--apply"], session_factory=session_factory) == 0
    assert "data keys: 1 wrapped anew" in capsys.readouterr().out


# --- a tenant's new data key ----------------------------------------------


def key_ids(session_factory) -> set[str]:
    return {crypto.key_id_of(value) for value in raw_values(session_factory)}


def test_a_new_data_key_seals_the_tenants_data_again(stored):
    engine, session_factory, document_id, before = stored
    keyring = use(engine, OLD)
    (first,) = key_ids(session_factory)
    done = rotate_tenant(session_factory, "default", apply=True)
    assert done.version == 2
    (second,) = key_ids(session_factory)
    assert second != first
    assert second == keyring.key_for(db.DEFAULT_TENANT_ID)
    assert read_all(session_factory, document_id) == before
    # The old version stays: a backup's values, or a late writer's, still open.
    versions = sorted(key.version for key in crypto.DatabaseKeyStore(engine).all())
    assert versions == [1, 2]
    assert seal_documents(session_factory, apply=False).changed["default"] == 0


def test_without_apply_a_tenant_rotation_counts_and_writes_nothing(stored):
    engine, session_factory, *_ = stored
    use(engine, OLD)
    before = key_ids(session_factory)
    done = rotate_tenant(session_factory, "default", apply=False)
    assert done.version is None
    assert done.report.changed["default"] == len(raw_values(session_factory))
    assert key_ids(session_factory) == before
    assert len(crypto.DatabaseKeyStore(engine).all()) == 1


def test_what_a_process_sealed_with_the_old_key_meanwhile_is_sealed_again(stored):
    engine, session_factory, document_id, _ = stored
    late = use(engine, OLD)
    late.key_for(db.DEFAULT_TENANT_ID)  # this process holds version 1 as current
    rotate = crypto.Keyring(OLD, crypto.DatabaseKeyStore(engine))
    crypto.configure(rotate)
    rotate_tenant(session_factory, "default", apply=True)
    crypto.configure(late)  # it writes once more with version 1
    stored_invoice_again = ingest(
        session_factory, inline_queue(session_factory), XML + b" ", "s.xml"
    ).document_id
    crypto.configure(rotate)
    report = seal_documents(session_factory, apply=True)
    assert report.rekeyed["default"] > 0
    assert key_ids(session_factory) == {rotate.key_for(db.DEFAULT_TENANT_ID)}
    assert read_all(session_factory, stored_invoice_again)[1] == "s.xml"


def test_a_running_process_takes_up_a_new_current_key(stored):
    engine, *_ = stored
    now = [0.0]
    running = crypto.Keyring(OLD, crypto.DatabaseKeyStore(engine), clock=lambda: now[0])
    first = running.key_for(db.DEFAULT_TENANT_ID)
    crypto.Keyring(OLD, crypto.DatabaseKeyStore(engine)).rotate(db.DEFAULT_TENANT_ID)
    assert running.key_for(db.DEFAULT_TENANT_ID) == first  # within the minute
    now[0] = crypto.Keyring.CURRENT_FOR + 1
    assert running.key_for(db.DEFAULT_TENANT_ID) != first


def test_rotating_a_tenant_on_the_command_line(stored, capsys):
    engine, session_factory, *_ = stored
    use(engine, OLD)
    assert cli(["key-rotate-tenant", "default", "--apply"], session_factory=session_factory) == 0
    assert "default: data key version 2, " in capsys.readouterr().out
    assert cli(["key-rotate-tenant", "nobody"], session_factory=session_factory) == 2


# --- backups --------------------------------------------------------------


def restored(stored, tmp_path):
    """The database file copied away and back, as a backup and its restore."""
    engine, *_ = stored
    engine.dispose()
    shutil.copy(tmp_path / "einvoice.db", tmp_path / "backup.db")
    return database(tmp_path / "backup.db")


def test_a_backup_restores_with_its_master_key(stored, tmp_path):
    *_, document_id, before = stored
    engine, session_factory = restored(stored, tmp_path)
    use(engine, OLD)
    assert read_all(session_factory, document_id) == before


def test_a_backup_opens_with_no_other_key(stored, tmp_path):
    *_, document_id, _ = stored
    engine, session_factory = restored(stored, tmp_path)
    use(engine, NEW)
    with pytest.raises(crypto.CryptoError):
        read_all(session_factory, document_id)


def test_a_backup_from_before_a_master_rotation_needs_the_old_key(stored, tmp_path):
    engine, session_factory, document_id, before = stored
    shutil.copy(tmp_path / "einvoice.db", tmp_path / "backup.db")  # taken before
    use(engine, NEW, OLD)
    rotate_master(apply=True)
    backup, backup_factory = database(tmp_path / "backup.db")
    use(backup, NEW)
    with pytest.raises(crypto.CryptoError, match="MASTER_KEY_PREVIOUS"):
        read_all(backup_factory, document_id)
    use(backup, NEW, OLD)
    assert read_all(backup_factory, document_id) == before
