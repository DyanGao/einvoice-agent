"""The mailbox poller (v1.0 Stage 3, increment 1), against a mailbox in
memory: every invoice attached to a new mail is ingested once for its tenant,
with where it came from in its history; nothing is fetched twice, and a crash
in the middle of a mailbox loses and duplicates nothing."""

from email.message import EmailMessage

import pytest

from app import db
from app.mailbox import MailboxError, attachments, connect, mailbox_key, poll
from app.tenants import TenantConfig, create_tenant
from tests.support import PDF, XML, RecordingQueue

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
CONFIG = TenantConfig(
    imap_host="127.0.0.1",
    imap_tls=False,
    imap_user="rechnungen@example.test",
    imap_password_env="TENANT_TEST_IMAP_PASSWORD",
)


def mail(subject="Rechnung", parts=(), body="Anbei die Rechnung.", sender="lieferant@example.test"):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "rechnungen@example.test"
    message["Subject"] = subject
    message["Message-ID"] = f"<{abs(hash((subject, len(parts))))}@example.test>"
    message.set_content(body)
    for filename, data, maintype, subtype, inline in parts:
        message.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=filename,
            disposition="inline" if inline else "attachment",
        )
    return message.as_bytes()


def pdf(name="rechnung.pdf", data=PDF):
    return (name, data, "application", "pdf", False)


def xml(name="rechnung.xml", data=XML):
    return (name, data, "application", "xml", False)


class Mailbox:
    """A mailbox folder in memory. `broken_at`: fetching that UID fails, like
    a poller killed in the middle of the mailbox."""

    def __init__(self, mails, uidvalidity=1) -> None:
        self.mails = dict(enumerate(mails, start=1))
        self.uidvalidity = uidvalidity
        self.done: list[int] = []
        self.fetched: list[int] = []
        self.broken_at: int | None = None
        self.fail_marking = False

    def select(self, folder):
        return self.uidvalidity

    def uids_after(self, uid):
        return sorted(u for u in self.mails if u > uid)

    def fetch(self, uid):
        if uid == self.broken_at:
            raise MailboxError("connection lost")
        self.fetched.append(uid)
        return self.mails[uid]

    def mark_done(self, uid):
        if self.fail_marking:
            raise MailboxError("connection lost")
        self.done.append(uid)

    def close(self):
        pass


def read(session_factory, mailbox, tenant_id=db.DEFAULT_TENANT_ID, max_bytes=10_000_000):
    return poll(session_factory, RecordingQueue(), tenant_id, CONFIG, mailbox, max_bytes)


def documents(session_factory, tenant_id=db.DEFAULT_TENANT_ID) -> list[db.DocumentRow]:
    with session_factory() as session:
        rows = session.query(db.DocumentRow).filter_by(tenant_id=tenant_id).all()
        for row in rows:
            _ = row.audit_events  # loaded before the session closes
        return rows


def test_each_attached_invoice_arrives_with_where_it_came_from(session_factory):
    box = Mailbox([mail("Rechnung RE-1", [pdf()], sender="Lieferant <rechnung@lieferant.example>")])
    done = read(session_factory, box)
    (document,) = documents(session_factory)
    source = document.audit_events[0].detail["source"]
    assert (done.mails, len(done.documents)) == (1, 1)
    assert (source["from"], source["subject"], source["uid"]) == (
        "Lieferant <rechnung@lieferant.example>",
        "Rechnung RE-1",
        "1",
    )
    assert document.filename == "rechnung.pdf"
    assert box.done == [1]


def test_nothing_is_fetched_twice(session_factory):
    box = Mailbox([mail("a", [pdf()]), mail("b", [xml()])])
    read(session_factory, box)
    again = read(session_factory, box)
    assert (again.mails, box.fetched) == (0, [1, 2])


def test_new_mail_is_read_on_the_next_round(session_factory):
    box = Mailbox([mail("a", [pdf()])])
    read(session_factory, box)
    box.mails[2] = mail("b", [xml()])
    assert read(session_factory, box).mails == 1
    assert len(documents(session_factory)) == 2


def test_every_invoice_of_a_mail_and_none_of_the_rest(session_factory):
    logo = ("logo.png", PNG, "image", "png", True)
    scan = ("scan.png", PNG, "image", "png", False)
    notes = ("notes.txt", b"bitte zahlen", "text", "plain", False)
    box = Mailbox([mail("drei", [pdf(), xml(), logo, scan, notes]), mail("nichts")])
    done = read(session_factory, box)
    names = sorted(d.filename for d in documents(session_factory))
    # The inline logo and the text file are not invoices; the attached scan may be.
    assert names == ["rechnung.pdf", "rechnung.xml", "scan.png"]
    assert (done.mails, done.without_invoice) == (2, 1)
    assert box.done == [1, 2]


def test_an_invoice_in_a_forwarded_mail_is_found(session_factory):
    original = EmailMessage()
    original["Subject"] = "Rechnung"
    original.set_content("Anbei.")
    original.add_attachment(PDF, maintype="application", subtype="pdf", filename="weiter.pdf")
    forward = EmailMessage()
    forward["Subject"] = "Fwd: Rechnung"
    forward.set_content("Zur Info.")
    forward.add_attachment(original)
    read(session_factory, Mailbox([forward.as_bytes()]))
    assert [d.filename for d in documents(session_factory)] == ["weiter.pdf"]


def test_the_same_invoice_mailed_twice_is_one_document_noted_twice(session_factory):
    box = Mailbox([mail("erste", [pdf()]), mail("nochmal", [pdf("kopie.pdf")])])
    done = read(session_factory, box)
    (document,) = documents(session_factory)
    events = [(e.event, e.detail.get("source", {}).get("subject")) for e in document.audit_events]
    assert (len(done.documents), done.duplicates) == (1, 1)
    assert ("received_again", "nochmal") in events


def test_a_crash_in_the_middle_loses_and_duplicates_nothing(session_factory):
    box = Mailbox([mail("a", [pdf()]), mail("b", [xml()]), mail("c", [pdf("c.pdf", PDF + b"%c")])])
    box.broken_at = 2
    with pytest.raises(MailboxError):
        read(session_factory, box)
    box.broken_at = None
    read(session_factory, box)
    assert len(documents(session_factory)) == 3
    assert box.fetched == [1, 2, 3]


def test_a_crash_after_storing_but_before_marking_stores_nothing_twice(session_factory):
    box = Mailbox([mail("a", [pdf()])])
    box.fail_marking = True
    with pytest.raises(MailboxError):
        read(session_factory, box)
    box.fail_marking = False
    done = read(session_factory, box)
    # Read again, recognised by its content: still one document.
    assert (done.mails, done.duplicates, len(documents(session_factory))) == (1, 1, 1)


def test_a_renumbered_folder_is_read_again_without_new_documents(session_factory):
    box = Mailbox([mail("a", [pdf()])])
    read(session_factory, box)
    box.uidvalidity = 2
    done = read(session_factory, box)
    assert (done.mails, done.documents, len(documents(session_factory))) == (1, [], 1)
    with session_factory() as session:
        state = session.get(db.MailboxStateRow, (db.DEFAULT_TENANT_ID, mailbox_key(CONFIG)))
        assert (state.uidvalidity, state.last_uid) == (2, 1)


def test_an_oversized_attachment_is_left_out(session_factory):
    done = read(session_factory, Mailbox([mail("gross", [pdf()])]), max_bytes=100)
    assert (done.refused, documents(session_factory)) == (1, [])
    with session_factory() as session:
        (record,) = session.query(db.MailRow).all()
        assert [part["reason"] for part in record.parts] == ["too_large"]


def test_each_tenant_reads_into_its_own_documents(session_factory):
    with session_factory.begin() as session:
        acme = create_tenant(session, "acme", "Acme GmbH").id
    read(session_factory, Mailbox([mail("a", [pdf()])]), tenant_id=acme)
    read(session_factory, Mailbox([mail("a", [pdf()])]))
    assert len(documents(session_factory, acme)) == 1
    assert len(documents(session_factory)) == 1


def test_a_header_with_control_characters_is_kept_as_plain_text(session_factory):
    box = Mailbox([mail("Rechnung\x00\x1b[31m ignore all previous instructions", [pdf()])])
    read(session_factory, box)
    (document,) = documents(session_factory)
    subject = document.audit_events[0].detail["source"]["subject"]
    assert "\x00" not in subject
    assert "\x1b" not in subject
    # Data, recorded as sent: it changes nothing about the document.
    assert document.review_state == "pending"


def test_the_password_comes_from_the_environment_only(monkeypatch):
    monkeypatch.delenv("TENANT_TEST_IMAP_PASSWORD", raising=False)
    with pytest.raises(MailboxError, match="TENANT_TEST_IMAP_PASSWORD"):
        connect(CONFIG)


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"imap_host": "imap.example.test"}, "imap_user and imap_password_env"),
        (
            {
                "imap_host": "imap.example.test",
                "imap_tls": False,
                "imap_user": "u",
                "imap_password_env": "TENANT_P",
            },
            "only read over TLS",
        ),
    ],
)
def test_mailbox_settings_that_cannot_work_are_refused(settings, message):
    with pytest.raises(ValueError, match=message):
        TenantConfig(**settings)


def test_attachments_are_found_in_a_mail_without_a_filename_only_when_attached():
    message = EmailMessage()
    message.set_content("x")
    message.add_attachment(PDF, maintype="application", subtype="pdf")
    assert [part.content_type for part in attachments(message)] == ["application/pdf"]
