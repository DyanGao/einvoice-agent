"""Originals sealed at rest (v1.0 Stage 6, increment 1): stored sealed with
the tenant's key, read back as received, old plain rows sealed by
`einvoice seal`, and a database dump without the key shows no original."""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app import crypto
from app.api.main import create_app
from app.cli import main as cli
from app.db import Base, DocumentRow, TenantRow
from app.db.tables import DEFAULT_TENANT_ID
from app.ingest import ingest
from app.sealing import seal_documents
from tests.support import PDF, XML, inline_queue, settings_for

# Printed in the original, never read into a field: only the original holds it.
MARKER = b"<!-- only-in-the-original-7f3a9c -->"
ORIGINAL = XML.replace(b"?>", b"?>" + MARKER, 1)
assert MARKER in ORIGINAL


def raw_content(session_factory, document_id: str) -> bytes:
    with session_factory() as session:
        return session.scalar(
            text("SELECT content FROM documents WHERE id = :id"), {"id": document_id}
        )


def stored(session_factory, data: bytes = ORIGINAL, tenant_id: str = DEFAULT_TENANT_ID) -> str:
    queue = inline_queue(session_factory)
    return ingest(session_factory, queue, data, "rechnung.xml", tenant_id=tenant_id).document_id


def test_the_original_is_stored_sealed(session_factory):
    document_id = stored(session_factory)
    raw = raw_content(session_factory, document_id)
    assert crypto.is_sealed(raw)
    assert MARKER not in raw


def test_the_original_is_read_back_as_received(session_factory, tmp_path):
    document_id = stored(session_factory)
    with session_factory() as session:
        assert session.get(DocumentRow, document_id).content == ORIGINAL
    app = create_app(session_factory, inline_queue(session_factory), settings_for(tmp_path))
    response = TestClient(app).get(f"/documents/{document_id}/content")
    assert response.content == ORIGINAL


def test_a_pdf_is_processed_from_its_sealed_original(session_factory):
    document_id = stored(session_factory, PDF)
    with session_factory() as session:
        document = session.get(DocumentRow, document_id)
        assert document.status == "extracted"
        assert document.draft is not None


def test_each_tenant_seals_with_its_own_key(session_factory):
    with session_factory.begin() as session:
        session.add(TenantRow(id="b", slug="b", name="B"))
    mine = raw_content(session_factory, stored(session_factory))
    theirs = raw_content(session_factory, stored(session_factory, tenant_id="b"))
    key_of = slice(len(crypto.MAGIC), len(crypto.MAGIC) + 16)
    assert mine[key_of] != theirs[key_of]


def _plain_document(session_factory) -> str:
    """As stored before v1.0 Stage 6."""
    document_id = stored(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE documents SET content = :content WHERE id = :id"),
            {"content": ORIGINAL, "id": document_id},
        )
    return document_id


def test_a_document_stored_before_sealing_is_still_read(session_factory):
    document_id = _plain_document(session_factory)
    with session_factory() as session:
        assert session.get(DocumentRow, document_id).content == ORIGINAL


def test_seal_counts_then_seals_what_is_plain(session_factory):
    document_id = _plain_document(session_factory)
    stored(session_factory, PDF)

    report = seal_documents(session_factory, apply=False)
    assert (report.changed["default"], report.already["default"]) == (1, 1)
    assert not crypto.is_sealed(raw_content(session_factory, document_id))

    seal_documents(session_factory, apply=True)
    assert crypto.is_sealed(raw_content(session_factory, document_id))
    with session_factory() as session:
        assert session.get(DocumentRow, document_id).content == ORIGINAL
    assert seal_documents(session_factory, apply=True).changed["default"] == 0


def test_open_stores_every_original_plain_again(session_factory):
    document_id = stored(session_factory)
    seal_documents(session_factory, apply=True, open_all=True)
    assert raw_content(session_factory, document_id) == ORIGINAL


def test_seal_on_the_command_line(session_factory, capsys):
    _plain_document(session_factory)
    assert cli(["seal"], session_factory=session_factory) == 0
    assert "default: 1 to seal, 0 sealed already" in capsys.readouterr().out
    assert cli(["seal", "--apply"], session_factory=session_factory) == 0
    assert "default: 1 sealed, 0 sealed already" in capsys.readouterr().out


def test_key_generate_prints_a_master_key(capsys):
    assert cli(["key-generate"]) == 0
    crypto.MasterKey.parse(capsys.readouterr().out)


def _dump(path) -> bytes:
    connection = sqlite3.connect(path)
    try:
        return "\n".join(connection.iterdump()).encode()
    finally:
        connection.close()


@pytest.mark.parametrize("encryption", ["on", "off"])
def test_a_database_dump_without_the_key_holds_no_original(tmp_path, keyring, encryption):
    path = tmp_path / "einvoice.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    if encryption == "off":
        crypto.configure(None)  # the control: the same dump shows it plain
    stored(session_factory)
    engine.dispose()
    dump = _dump(path)
    # iterdump writes BLOBs as hex.
    assert (MARKER.hex().upper().encode() in dump) == (encryption == "off")
    assert b"only-in-the-original" not in dump  # not as text either


def test_documents_select_by_their_id_without_opening(session_factory):
    """Lists and lookups never need the key: only reading the original does."""
    stored(session_factory)
    crypto.configure(None)
    with session_factory() as session:
        assert session.scalars(select(DocumentRow.id)).all()
