"""The mailbox eval itself (v1.0 Stage 3, increment 4): the mailbox it
generates, and that its gate fails when a mail comes out wrong."""

from collections import Counter

from eval import mailbox


def test_the_mailbox_has_every_kind_of_mail_as_planned():
    cases = mailbox.generate()
    assert len(cases) == mailbox.MAILS
    assert Counter(case.kind for case in cases) == Counter(mailbox.PLAN)


def test_the_mailbox_is_the_same_every_time_but_for_the_encrypted_pdfs():
    first, second = mailbox.generate(), mailbox.generate()
    differing = {a.kind for a, b in zip(first, second, strict=True) if a.raw != b.raw}
    assert differing <= {"encrypted_pdf"}


def test_no_copy_comes_before_the_invoice_it_copies():
    cases = mailbox.generate()
    first_invoice = next(i for i, case in enumerate(cases) if case.new_invoices)
    assert all(case.kind != "again" for case in cases[:first_invoice])


def test_the_score_finds_a_mail_that_came_out_wrong():
    cases = mailbox.generate()[:20]
    session_factory = mailbox._database()  # noqa: SLF001 - the eval's own in-memory database
    mailbox.read_in_memory(session_factory, cases, restarts=0)
    assert mailbox.score(session_factory, cases)["wrong"] == []
    # Expect something else of one mail: the score must say so.
    cases[0].expected = [("nicht-da.xml", "ingested", None)]
    wrong = mailbox.score(session_factory, cases)["wrong"]
    assert [item["uid"] for item in wrong] == [1]
