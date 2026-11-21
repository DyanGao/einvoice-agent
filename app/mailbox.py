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

import base64
import contextlib
import email
import imaplib
import io
import logging
import mimetypes
import os
import re
import ssl
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from email.message import Message
from email.policy import default as default_policy
from pathlib import Path
from typing import Protocol

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy.orm import Session, sessionmaker

from app.db.rls import tenant_transaction
from app.db.tables import DocumentRow, MailboxStateRow, MailRow
from app.ingest import ingest
from app.mailbox_oauth import TokenError, TokenSource, xoauth2
from app.queue import JobQueue
from app.tenants import TenantConfig

logger = logging.getLogger(__name__)

# What may be an invoice. Anything else in a mail (text, signatures, calendar
# invites) is left alone.
ACCEPTED_TYPES = {"application/xml", "text/xml", "application/pdf", "image/png", "image/jpeg"}
ACCEPTED_SUFFIXES = {".xml", ".pdf", ".png", ".jpg", ".jpeg"}
# Set on a handled mail that stays in its folder, with \Seen.
DONE_KEYWORD = "$EInvoiceDone"
ZIP_TYPES = {"application/zip", "application/x-zip-compressed"}
# A ZIP is opened one level deep, and only so far: at most this many files,
# no file larger than the upload limit, all together at most this many
# times the upload limit, and none that unpacks to more than this many times
# its packed size (a ZIP bomb).
ZIP_MAX_MEMBERS = 50
ZIP_MAX_TOTAL_FACTOR = 4
ZIP_MAX_RATIO = 100
INGESTED, KNOWN, REFUSED = "ingested", "known", "refused"
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
        password: str | None = None,
        processed_folder: str | None = None,
        timeout: float = 30.0,
        token: str | None = None,
    ) -> None:
        """Signs in with the password, or with an OAuth2 access token
        (XOAUTH2) when one is given."""
        if (password is None) == (token is None):
            raise ValueError("sign in with a password or a token, not both or neither")
        if tls:
            self._imap: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                host, port, ssl_context=ssl.create_default_context(), timeout=timeout
            )
        else:
            self._imap = imaplib.IMAP4(host, port, timeout=timeout)
        try:
            if token is not None:
                self._xoauth2(user, token)
            else:
                self._imap.login(user, password)
        except imaplib.IMAP4.error as exc:
            raise MailboxError(f"sign-in to {host} refused") from exc
        self._processed_folder = processed_folder
        self._can_move = "MOVE" in self._imap.capabilities

    def _xoauth2(self, user: str, token: str) -> None:
        """AUTHENTICATE XOAUTH2. Where the server offers SASL-IR (RFC 4959;
        Gmail and Outlook do, and some servers take nothing else), the token
        goes with the command. imaplib only knows the other way, the server
        asking for it first, so this does what imaplib's own `authenticate`
        does (`literal`, `_simple_command`, `state`), with the token as the
        command's argument. A refusal comes as a challenge; it is answered
        with an empty line, as XOAUTH2 asks, and then the server says NO."""
        if "SASL-IR" not in self._imap.capabilities:
            self._imap.authenticate("XOAUTH2", lambda _: xoauth2(user, token))
            return
        initial = base64.b64encode(xoauth2(user, token)).decode("ascii")
        self._imap.literal = imaplib._Authenticator(lambda _: b"").process  # noqa: SLF001
        status, data = self._imap._simple_command("AUTHENTICATE", "XOAUTH2", initial)  # noqa: SLF001
        if status != "OK":
            raise imaplib.IMAP4.error(data[-1].decode("utf-8", "replace") if data else status)
        self._imap.state = "AUTH"

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


def _is_invoice_type(content_type: str, filename: str | None) -> bool:
    suffix = Path(filename).suffix.lower() if filename else ""
    return content_type in ACCEPTED_TYPES or suffix in ACCEPTED_SUFFIXES


def _is_zip(content_type: str, filename: str | None) -> bool:
    suffix = Path(filename).suffix.lower() if filename else ""
    return content_type in ZIP_TYPES or suffix == ".zip"


def attachments(message: Message) -> Iterator[Attachment]:
    """Every part of a mail that was attached, forwarded mails included: what
    has a filename or is marked as an attachment. The text of the mail and
    inline images (logos, signatures) are not attachments."""
    for part in message.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        # Images only when really attached: logos in a signature are embedded.
        if part.get_content_maintype() == "image" and disposition != "attachment":
            continue
        if disposition == "inline" and part.get_content_maintype() == "text":
            continue
        if filename is None and disposition != "attachment":
            continue
        try:
            data = part.get_payload(decode=True) or b""
        except (ValueError, TypeError):
            data = b""
        yield Attachment(filename, part.get_content_type(), data)


@dataclass
class Part:
    """What became of one attached file. outcome: ingested | known | refused."""

    filename: str | None
    content_type: str
    size: int
    outcome: str
    reason: str | None = None
    document_id: str | None = None

    def as_record(self) -> dict:
        return {
            "filename": self.filename,
            "content_type": self.content_type,
            "bytes": self.size,
            "outcome": self.outcome,
            "reason": self.reason,
            "document_id": self.document_id,
        }


def _encrypted_pdf(data: bytes) -> bool:
    """A PDF that needs a password cannot be read, by a person or a model."""
    if not data.startswith(b"%PDF-"):
        return False
    try:
        return PdfReader(io.BytesIO(data)).is_encrypted
    except (PdfReadError, ValueError, KeyError, TypeError):
        return False  # broken in another way: the pipeline says so


def _unzipped(item: Attachment, max_bytes: int) -> tuple[list[Attachment], list[Part]]:
    """The files in a ZIP, one level deep. A ZIP's own idea of its sizes is
    not trusted: every member is read with a limit."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(item.data))
        members = [info for info in archive.infolist() if not info.is_dir()]
    except (zipfile.BadZipFile, ValueError, OSError):
        return [], [Part(item.filename, item.content_type, len(item.data), REFUSED, "unreadable")]
    if len(members) > ZIP_MAX_MEMBERS:
        reason = f"zip_too_many_files ({len(members)}, at most {ZIP_MAX_MEMBERS})"
        return [], [Part(item.filename, item.content_type, len(item.data), REFUSED, reason)]
    files, refused, total = [], [], 0
    for info in members:
        name = Path(info.filename).name or None
        content_type = mimetypes.guess_type(name or "")[0] or "application/octet-stream"
        if info.flag_bits & 0x1:
            refused.append(Part(name, content_type, info.file_size, REFUSED, "encrypted_zip"))
            continue
        if _is_zip(content_type, name):
            refused.append(Part(name, content_type, info.file_size, REFUSED, "nested_zip"))
            continue
        if not _is_invoice_type(content_type, name):
            refused.append(Part(name, content_type, info.file_size, REFUSED, "unsupported_type"))
            continue
        try:
            with archive.open(info) as member:
                data = member.read(max_bytes + 1)
        except (zipfile.BadZipFile, ValueError, OSError, RuntimeError):
            refused.append(Part(name, content_type, info.file_size, REFUSED, "unreadable"))
            continue
        total += len(data)
        if len(data) > max_bytes or total > ZIP_MAX_TOTAL_FACTOR * max_bytes:
            refused.append(Part(name, content_type, len(data), REFUSED, "too_large"))
            continue
        if info.compress_size and len(data) > ZIP_MAX_RATIO * info.compress_size:
            refused.append(Part(name, content_type, len(data), REFUSED, "zip_bomb"))
            continue
        files.append(Attachment(name, content_type, data))
    return files, refused


def _classify(item: Attachment, max_bytes: int) -> tuple[list[Attachment], list[Part]]:
    """The invoices among an attached file (several for a ZIP), and what is
    refused, with the reason."""
    size = len(item.data)
    refuse = lambda reason: ([], [Part(item.filename, item.content_type, size, REFUSED, reason)])  # noqa: E731
    if size == 0:
        return refuse("empty")
    if size > max_bytes:
        return refuse("too_large")
    if _is_zip(item.content_type, item.filename):
        return _unzipped(item, max_bytes)
    if not _is_invoice_type(item.content_type, item.filename):
        return refuse("unsupported_type")
    return [item], []


@dataclass
class Round:
    """What one look into a mailbox did."""

    mails: int = 0
    documents: list[str] = field(default_factory=list)
    duplicates: int = 0
    without_invoice: int = 0
    refused: int = 0


def mailbox_key(config: TenantConfig) -> str:
    return f"{config.imap_user}@{config.imap_host}/{config.imap_folder}"


def _start(
    session_factory: sessionmaker[Session], tenant_id: str, key: str, uidvalidity: int
) -> int:
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


def _first_from(
    session_factory: sessionmaker[Session], tenant_id: str, document_id: str, source: dict
) -> bool:
    """Whether a document already known came first from this very mail: read
    again after a crash between storing it and marking the mail. It is then
    this mail's invoice, not a copy someone sent again (found by the mailbox
    eval)."""
    with tenant_transaction(session_factory, tenant_id) as session:
        document = session.get(DocumentRow, document_id)
        first = {}
        if document is not None and document.audit_events:
            first = document.audit_events[0].detail.get("source") or {}
    same = ("mailbox", "uidvalidity", "uid")
    return all(first.get(key) == source[key] for key in same)


def _handled(
    session_factory: sessionmaker[Session],
    tenant_id: str,
    key: str,
    uid: int,
    record: MailRow,
) -> None:
    """The mail's record and the UID handled, in one transaction: a mail is
    recorded exactly when it counts as read."""
    with tenant_transaction(session_factory, tenant_id) as session:
        state = session.get(MailboxStateRow, (tenant_id, key))
        state.last_uid = uid
        record.uidvalidity = state.uidvalidity
        session.add(record)


def poll(
    session_factory: sessionmaker[Session],
    queue: JobQueue,
    tenant_id: str,
    config: TenantConfig,
    connection: MailConnection,
    max_bytes: int,
) -> Round:
    """Read the tenant's new mails once. Each mail: its invoices ingested,
    then the mail marked done, then the mail recorded with its UID — so a
    crash anywhere in between costs at most reading one mail again, which
    stores nothing new."""
    key = mailbox_key(config)
    uidvalidity = connection.select(config.imap_folder)
    last = _start(session_factory, tenant_id, key, uidvalidity)
    done = Round()
    for uid in connection.uids_after(last):
        message = email.message_from_bytes(connection.fetch(uid), policy=default_policy)
        source = {
            "channel": "email",
            "mailbox": key,
            "uidvalidity": str(uidvalidity),
            "uid": str(uid),
            "from": _header(message, "From"),
            "subject": _header(message, "Subject"),
            "message_id": _header(message, "Message-ID"),
            "date": _header(message, "Date"),
        }
        parts: list[Part] = []
        for item in attachments(message):
            invoices, refused = _classify(item, max_bytes)
            parts.extend(refused)
            for invoice in invoices:
                if _encrypted_pdf(invoice.data):
                    parts.append(
                        Part(
                            invoice.filename,
                            invoice.content_type,
                            len(invoice.data),
                            REFUSED,
                            "encrypted_pdf",
                        )
                    )
                    continue
                result = ingest(
                    session_factory,
                    queue,
                    invoice.data,
                    invoice.filename,
                    tenant_id=tenant_id,
                    source=source,
                )
                first_time = not result.duplicate or _first_from(
                    session_factory, tenant_id, result.document_id, source
                )
                outcome = INGESTED if first_time else KNOWN
                parts.append(
                    Part(
                        invoice.filename,
                        invoice.content_type,
                        len(invoice.data),
                        outcome,
                        document_id=result.document_id,
                    )
                )
                if not first_time:
                    done.duplicates += 1
                else:
                    done.documents.append(result.document_id)
        refused = [part for part in parts if part.outcome == REFUSED]
        done.refused += len(refused)
        if not any(part.outcome != REFUSED for part in parts):
            done.without_invoice += 1
        for part in refused:
            logger.info(
                "tenant %s, mail %s: %s refused (%s)", tenant_id, uid, part.filename, part.reason
            )
        connection.mark_done(uid)
        record = MailRow(
            tenant_id=tenant_id,
            mailbox=key,
            uid=uid,
            message_id=source["message_id"],
            sender=source["from"],
            subject=source["subject"],
            sent=source["date"],
            parts=[part.as_record() for part in parts],
        )
        _handled(session_factory, tenant_id, key, uid, record)
        done.mails += 1
    return done


def connect(config: TenantConfig, tokens: TokenSource | None = None) -> ImapConnection:
    """Open the tenant's mailbox: with its password, or with a token from
    `tokens` for imap_auth microsoft or google."""
    common = {
        "host": config.imap_host,
        "port": config.imap_port,
        "tls": config.imap_tls,
        "user": config.imap_user,
        "processed_folder": config.imap_processed_folder,
    }
    if config.imap_auth == "password":
        password = os.environ.get(config.imap_password_env or "")
        if not password:
            raise MailboxError(f"the mailbox password ({config.imap_password_env}) is not set")
        return ImapConnection(**common, password=password)
    if tokens is None:
        raise MailboxError(f"imap_auth={config.imap_auth} needs a token source")
    try:
        token = tokens.token()
    except TokenError as exc:
        raise MailboxError(str(exc)) from exc
    return ImapConnection(**common, token=token)


def main() -> None:
    from app import crypto
    from app.db.engine import database_engine
    from app.db.instance_lock import AlreadyRunning, InstanceLock, LockLost
    from app.queue import RqQueue
    from app.settings import Settings
    from app.tenants import TenantServices

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = Settings.from_env()
    engine = database_engine(settings.database_url)
    crypto.configure_from_env(engine)  # refuses to start without MASTER_KEY
    try:
        lock = InstanceLock(engine, "mailbox poller").acquire()
    except AlreadyRunning as exc:
        raise SystemExit(str(exc)) from exc
    session_factory = sessionmaker(engine)
    tenants = TenantServices(session_factory, settings)
    queue = RqQueue(settings.redis_url)
    # Kept from round to round, so a token is reused until it runs out.
    token_sources: dict[str, TokenSource] = {}
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
                tokens = None
                if config.imap_auth != "password":
                    key = config.model_dump_json()
                    tokens = token_sources.setdefault(key, TokenSource(config))
                connection = connect(config, tokens)
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
