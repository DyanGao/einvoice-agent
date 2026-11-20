"""The mailbox poller:  python -m app.mailbox  (or `make mailbox`).

Every MAILBOX_SECONDS it reads, tenant by tenant, the mailbox the tenant's
settings name (app/tenants.py: imap_*), and hands each invoice attached to a
new mail to `ingest`, like an upload: stored for the tenant, then queued.

- Nothing is fetched twice: IMAP numbers the mails of a folder with UIDs that
  only grow while the folder's UIDVALIDITY stays the same. The last UID
  handled is stored per tenant and mailbox (`mailbox_state`) after each mail,
  so a restart in the middle of a mailbox goes on where it stopped. A mail
  read again after a crash adds no document: `ingest` recognises the bytes.
- A handled mail is flagged, or moved to `imap_processed_folder` where the
  server can move; it is never deleted.
- Sender, subject and Message-ID go into the document's history as where it
  came from. They are data: nothing in a mail is read as an instruction.
- The password is in the environment, under the name the settings give
  (owner's decision, 2026-11-20); it is never stored or logged.

One poller per database (instance lock), like the bank watch.
"""

import contextlib
import email
import imaplib
import logging
import os
import re
import ssl
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from email.message import Message
from email.policy import default as default_policy
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.db.rls import tenant_transaction
from app.db.tables import MailboxStateRow
from app.ingest import ingest
from app.queue import JobQueue
from app.tenants import TenantConfig

logger = logging.getLogger(__name__)

# What may be an invoice. Anything else in a mail (text, signatures, calendar
# invites) is left alone.
ACCEPTED_TYPES = {"application/xml", "text/xml", "application/pdf", "image/png", "image/jpeg"}
ACCEPTED_SUFFIXES = {".xml", ".pdf", ".png", ".jpg", ".jpeg"}
# Set on a handled mail that stays in its folder, with \Seen.
DONE_KEYWORD = "$EInvoiceDone"
_HEADER_LENGTH = 300
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class MailboxError(Exception):
    """The server refused something the poller needs."""


class MailConnection(Protocol):
    """One open mailbox. `select` answers the folder's UIDVALIDITY."""

    def select(self, folder: str) -> int: ...

    def uids_after(self, uid: int) -> list[int]: ...

    def fetch(self, uid: int) -> bytes: ...

    def mark_done(self, uid: int) -> None: ...

    def close(self) -> None: ...


def _quoted(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


class ImapConnection:
    """MailConnection over imaplib. Mails are fetched with BODY.PEEK, so
    reading alone does not mark them as read."""

    def __init__(
        self,
        host: str,
        port: int,
        tls: bool,
        user: str,
        password: str,
        processed_folder: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        if tls:
            self._imap: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                host, port, ssl_context=ssl.create_default_context(), timeout=timeout
            )
        else:
            self._imap = imaplib.IMAP4(host, port, timeout=timeout)
        try:
            self._imap.login(user, password)
        except imaplib.IMAP4.error as exc:
            raise MailboxError(f"sign-in to {host} refused") from exc
        self._processed_folder = processed_folder
        self._can_move = "MOVE" in self._imap.capabilities

    def _ok(self, answer: tuple, what: str) -> list:
        status, data = answer
        if status != "OK":
            raise MailboxError(f"{what}: {status} {data!r}")
        return data

    def select(self, folder: str) -> int:
        self._ok(self._imap.select(_quoted(folder)), f"select {folder}")
        status, data = self._imap.response("UIDVALIDITY")
        if status != "UIDVALIDITY" or not data or data[0] is None:
            raise MailboxError(f"{folder}: the server sent no UIDVALIDITY")
        return int(data[0])

    def uids_after(self, uid: int) -> list[int]:
        data = self._ok(self._imap.uid("SEARCH", None, f"UID {uid + 1}:*"), "search")
        found = [int(item) for item in (data[0] or b"").split()]
        # "n:*" also answers the newest mail when its UID is below n.
        return sorted(item for item in found if item > uid)

    def fetch(self, uid: int) -> bytes:
        data = self._ok(self._imap.uid("FETCH", str(uid), "(BODY.PEEK[])"), f"fetch {uid}")
        for item in data:
            if isinstance(item, tuple):
                return item[1]
        raise MailboxError(f"mail {uid} came back empty")

    def mark_done(self, uid: int) -> None:
        if self._processed_folder is not None and self._can_move:
            self._ok(self._imap.uid("MOVE", str(uid), _quoted(self._processed_folder)), "move")
            return
        if self._processed_folder is not None:
            logger.warning("the server cannot move mails: mail %s is flagged instead", uid)
        status, _ = self._imap.uid("STORE", str(uid), "+FLAGS", f"(\\Seen {DONE_KEYWORD})")
        if status != "OK":
            # A server that allows no keywords of its own.
            self._ok(self._imap.uid("STORE", str(uid), "+FLAGS", "(\\Seen)"), "flag")

    def close(self) -> None:
        # Logging out of a connection that already broke is not worth an error.
        with contextlib.suppress(imaplib.IMAP4.error, OSError):
            self._imap.logout()


@dataclass(frozen=True)
class Attachment:
    filename: str | None
    content_type: str
    data: bytes


def _header(message: Message, name: str) -> str:
    try:
        value = str(message.get(name, "") or "")
    except (ValueError, TypeError):  # a header the parser cannot make sense of
        value = ""
    return _CONTROL.sub(" ", value).strip()[:_HEADER_LENGTH]


def attachments(message: Message) -> Iterator[Attachment]:
    """The parts of a mail that may be invoices, forwarded mails included.
    Images count only as real attachments, not inline (logos, signatures)."""
    for part in message.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        content_type = part.get_content_type()
        disposition = part.get_content_disposition()
        suffix = Path(filename).suffix.lower() if filename else ""
        if content_type not in ACCEPTED_TYPES and suffix not in ACCEPTED_SUFFIXES:
            continue
        if filename is None and disposition != "attachment":
            continue
        if content_type.startswith("image/") and disposition != "attachment":
            continue
        data = part.get_payload(decode=True)
        if data:
            yield Attachment(filename, content_type, data)


@dataclass
class Round:
    """What one look into a mailbox did."""

    mails: int = 0
    documents: list[str] = field(default_factory=list)
    duplicates: int = 0
    without_invoice: int = 0
    too_large: int = 0


def mailbox_key(config: TenantConfig) -> str:
    return f"{config.imap_user}@{config.imap_host}/{config.imap_folder}"


def _start(session_factory: sessionmaker[Session], tenant_id: str, key: str, uidvalidity: int):
    """The last UID handled in this mailbox folder, 0 when it is new or the
    server renumbered it (UIDVALIDITY changed)."""
    with tenant_transaction(session_factory, tenant_id) as session:
        state = session.get(MailboxStateRow, (tenant_id, key))
        if state is None:
            session.add(MailboxStateRow(tenant_id=tenant_id, mailbox=key, uidvalidity=uidvalidity))
            return 0
        if state.uidvalidity != uidvalidity:
            logger.warning(
                "tenant %s, %s: UIDVALIDITY changed, reading the folder again "
                "(invoices already stored are recognised by their content)",
                tenant_id,
                key,
            )
            state.uidvalidity, state.last_uid = uidvalidity, 0
            return 0
        return state.last_uid


def _handled(session_factory: sessionmaker[Session], tenant_id: str, key: str, uid: int) -> None:
    with tenant_transaction(session_factory, tenant_id) as session:
        session.get(MailboxStateRow, (tenant_id, key)).last_uid = uid


def poll(
    session_factory: sessionmaker[Session],
    queue: JobQueue,
    tenant_id: str,
    config: TenantConfig,
    connection: MailConnection,
    max_bytes: int,
) -> Round:
    """Read the tenant's new mails once. Each mail: its invoices ingested,
    then the mail marked done, then its UID recorded — so a crash anywhere
    in between costs at most reading one mail again, which stores nothing new."""
    key = mailbox_key(config)
    last = _start(session_factory, tenant_id, key, connection.select(config.imap_folder))
    done = Round()
    for uid in connection.uids_after(last):
        message = email.message_from_bytes(connection.fetch(uid), policy=default_policy)
        source = {
            "channel": "email",
            "mailbox": key,
            "uid": str(uid),
            "from": _header(message, "From"),
            "subject": _header(message, "Subject"),
            "message_id": _header(message, "Message-ID"),
            "date": _header(message, "Date"),
        }
        found = 0
        for part in attachments(message):
            if len(part.data) > max_bytes:
                done.too_large += 1
                logger.warning("tenant %s, mail %s: %s is too large", tenant_id, uid, part.filename)
                continue
            found += 1
            result = ingest(
                session_factory,
                queue,
                part.data,
                part.filename,
                tenant_id=tenant_id,
                source=source,
            )
            if result.duplicate:
                done.duplicates += 1
            else:
                done.documents.append(result.document_id)
        if found == 0:
            done.without_invoice += 1
            logger.info("tenant %s, mail %s: no invoice attached", tenant_id, uid)
        connection.mark_done(uid)
        _handled(session_factory, tenant_id, key, uid)
        done.mails += 1
    return done


def connect(config: TenantConfig) -> ImapConnection:
    password = os.environ.get(config.imap_password_env or "")
    if not password:
        raise MailboxError(f"the mailbox password ({config.imap_password_env}) is not set")
    return ImapConnection(
        config.imap_host,
        config.imap_port,
        config.imap_tls,
        config.imap_user,
        password,
        config.imap_processed_folder,
    )


def main() -> None:
    from app.db.engine import database_engine
    from app.db.instance_lock import AlreadyRunning, InstanceLock, LockLost
    from app.queue import RqQueue
    from app.settings import Settings
    from app.tenants import TenantServices

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = Settings.from_env()
    engine = database_engine(settings.database_url)
    try:
        lock = InstanceLock(engine, "mailbox poller").acquire()
    except AlreadyRunning as exc:
        raise SystemExit(str(exc)) from exc
    session_factory = sessionmaker(engine)
    tenants = TenantServices(session_factory, settings)
    queue = RqQueue(settings.redis_url)
    logger.info("reading every tenant's mailbox every %ss", settings.mailbox_seconds)
    while True:
        try:
            lock.check()
        except LockLost as exc:
            raise SystemExit(str(exc)) from exc
        for tenant_id in tenants.tenant_ids():
            config = tenants.config(tenant_id)
            if config.imap_host is None:
                continue
            try:
                connection = connect(config)
                try:
                    done = poll(
                        session_factory,
                        queue,
                        tenant_id,
                        config,
                        connection,
                        settings.max_upload_bytes,
                    )
                finally:
                    connection.close()
            except (MailboxError, imaplib.IMAP4.error, OSError) as exc:
                # One tenant's mailbox down does not keep the others waiting.
                logger.warning("tenant %s: mailbox not read: %s", tenant_id, exc)
                continue
            if done.mails:
                logger.info(
                    "tenant %s: %d mails, %d new documents, %d already known",
                    tenant_id,
                    done.mails,
                    len(done.documents),
                    done.duplicates,
                )
        time.sleep(settings.mailbox_seconds)


if __name__ == "__main__":
    main()
