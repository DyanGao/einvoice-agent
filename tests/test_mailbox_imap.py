"""The mailbox poller against a real IMAP server: GreenMail in Docker
(v1.0 Stage 3, increment 1). Skipped unless GREENMAIL_IMAP names it:

    docker run -d --name einvoice-greenmail -p 127.0.0.1:3143:3143 \\
      -e GREENMAIL_OPTS="-Dgreenmail.setup.test.imap -Dgreenmail.hostname=0.0.0.0 \\
      -Dgreenmail.auth.disabled" greenmail/standalone:2.1.5
    GREENMAIL_IMAP=127.0.0.1:3143 uv run pytest tests/test_mailbox_imap.py

With authentication off, GreenMail opens a fresh mailbox for every user name,
so each test signs in as a user of its own.
"""

import imaplib
import os
import re
import time
import uuid

import pytest

from app import db
from app.mailbox import DONE_KEYWORD, ImapConnection, MailboxError, poll
from app.tenants import TenantConfig
from tests.support import PDF, RecordingQueue
from tests.test_mailbox import mail, pdf, xml

SERVER = os.environ.get("GREENMAIL_IMAP")
pytestmark = pytest.mark.skipif(SERVER is None, reason="needs GreenMail (GREENMAIL_IMAP)")
PASSWORD = "greenmail-test"  # noqa: S105 -- GreenMail runs with authentication off


@pytest.fixture
def user():
    return f"t{uuid.uuid4().hex[:12]}@example.test"


def host_port():
    host, port = SERVER.rsplit(":", 1)
    return host, int(port)


def deliver(user, *messages, folders=()):
    host, port = host_port()
    imap = imaplib.IMAP4(host, port)
    imap.login(user, PASSWORD)
    for folder in folders:
        imap.create(folder)
    for message in messages:
        imap.append("INBOX", None, imaplib.Time2Internaldate(time.time()), message)
    imap.logout()


def config(user, processed=None):
    host, port = host_port()
    return TenantConfig(
        imap_host=host,
        imap_port=port,
        imap_tls=False,
        imap_user=user,
        imap_password_env="TENANT_GREENMAIL",
        imap_processed_folder=processed,
    )


def connection(cfg):
    return ImapConnection(
        cfg.imap_host, cfg.imap_port, False, cfg.imap_user, PASSWORD, cfg.imap_processed_folder
    )


def folder_state(user, folder="INBOX"):
    """UIDs and flags of the mails in a folder, as the server has them."""
    host, port = host_port()
    imap = imaplib.IMAP4(host, port)
    imap.login(user, PASSWORD)
    imap.select(folder, readonly=True)
    _, data = imap.uid("FETCH", "1:*", "(FLAGS)")
    imap.logout()
    return [item.decode() for item in data if item]


def run(session_factory, cfg, conn=None):
    conn = conn or connection(cfg)
    try:
        return poll(session_factory, RecordingQueue(), db.DEFAULT_TENANT_ID, cfg, conn, 10_000_000)
    finally:
        conn.close()


def stored(session_factory) -> list[str]:
    with session_factory() as session:
        return sorted(d.filename for d in session.query(db.DocumentRow).all())


def test_mails_are_read_once_and_flagged_not_deleted(session_factory, user):
    deliver(user, mail("eins", [pdf()]), mail("zwei", [xml()]))
    cfg = config(user)
    done = run(session_factory, cfg)
    assert (done.mails, stored(session_factory)) == (2, ["rechnung.pdf", "rechnung.xml"])
    flags = folder_state(user)
    assert len(flags) == 2
    assert all("\\Seen" in item and DONE_KEYWORD in item for item in flags)
    # A second look fetches nothing.
    assert run(session_factory, cfg).mails == 0


def test_handled_mails_are_moved_to_the_folder_named(session_factory, user):
    deliver(user, mail("eins", [pdf()]), folders=["Erledigt"])
    run(session_factory, config(user, processed="Erledigt"))
    assert folder_state(user) == []
    assert len(folder_state(user, "Erledigt")) == 1


class BreaksAt(ImapConnection):
    """A real connection that dies before fetching one mail."""

    def __init__(self, *args, broken_uid, **kwargs):
        super().__init__(*args, **kwargs)
        self.broken_uid = broken_uid

    def fetch(self, uid):
        if uid == self.broken_uid:
            raise MailboxError("connection lost")
        return super().fetch(uid)


def test_a_restart_in_the_middle_of_the_mailbox_loses_and_duplicates_nothing(session_factory, user):
    deliver(
        user,
        mail("eins", [pdf()]),
        mail("zwei", [xml()]),
        mail("drei", [pdf("drei.pdf", PDF + b"%drei")]),
    )
    cfg = config(user)
    host, port = host_port()
    second_uid = int(re.search(r"UID (\d+)", folder_state(user)[1]).group(1))
    broken = BreaksAt(host, port, False, user, PASSWORD, broken_uid=second_uid)
    with pytest.raises(MailboxError):
        run(session_factory, cfg, broken)
    assert stored(session_factory) == ["rechnung.pdf"]
    done = run(session_factory, cfg)
    assert done.mails == 2
    assert stored(session_factory) == ["drei.pdf", "rechnung.pdf", "rechnung.xml"]


def test_a_wrong_folder_is_an_error_not_an_empty_mailbox(session_factory, user):
    deliver(user, mail("eins", [pdf()]))
    cfg = config(user).model_copy(update={"imap_folder": "Gibt-es-nicht"})
    with pytest.raises(MailboxError, match="Gibt-es-nicht"):
        run(session_factory, cfg)
