"""Codes for texts stored before they had any (app/texts_backfill.py): a code
is found back from the English sentence only when it makes exactly that
sentence again, and then the UI shows the text in German."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app import db
from app.texts import ENGLISH, Text, placeholders
from app.texts_backfill import NESTED, NESTED_LISTS, backfill, parse


def _sample(code: str) -> Text:
    """A Text of this code with made-up parameters of the right kinds."""
    params: dict = {}
    for name in sorted(placeholders(ENGLISH[code])):
        if name.endswith("_amount"):
            params[name] = Decimal("1234.50")
        elif name.endswith("_percent"):
            params[name] = Decimal("0.125")
        elif name == "fields":
            params[name] = ["due_date", "lines.0.net"]
        elif name in NESTED_LISTS:
            params[name] = [
                Text("finding.duplicate", number="RE-1; RE-2"),
                Text("finding.po_not_found", po_ref="PO-7"),
            ]
        elif name in NESTED:
            # A text of the kinds this parameter holds.
            params[name] = _sample(next(c for c in sorted(ENGLISH) if c.startswith(NESTED[name])))
        else:
            params[name] = f"X-{name}-1"
    return Text(code, **params)


@pytest.mark.parametrize("code", sorted(ENGLISH))
def test_every_code_is_found_back_from_its_sentence(code):
    text = _sample(code)
    family = code.split(".")[0] + "."
    found = parse(str(text), (family,))
    assert found is not None
    assert (found.code, str(found)) == (code, str(text))


def test_texts_inside_a_text_are_found_too():
    text = Text(
        "reason.findings",
        details=[
            Text(
                "finding.vat_id_invalid",
                party=Text("party.seller"),
                vat_id="DE123",
                problem=Text("vat_id.german_check_digit"),
            ),
            # A plain problem (the ERP's own message), with "; " inside.
            Text("finding.master_data_unavailable", problem="timeout; ERP down"),
        ],
    )
    assert parse(str(text), ("reason.",)).said() == text.said()


def test_a_share_comes_back_as_the_fraction_it_was():
    text = Text(
        "finding.price_deviation",
        line=1,
        invoiced_amount=Decimal("95.76"),
        above_percent=Decimal("0.12"),
        ordered_amount=Decimal("85.50"),
        order="PO-672261",
    )
    found = parse(str(text), ("finding.",))
    assert found.params["above_percent"] == Decimal("0.12")
    assert found.said()["params"]["invoiced_amount"] == "95.76"


def test_a_sentence_of_no_template_has_no_code():
    assert parse("the supplier sent a cake", ("finding.",)) is None
    # A wording from before: the threshold was rounded then ("98%", now "97.5%").
    old = "read with less than 98% confidence: due_date"
    assert parse(old, ("reason.",)) is None


def _forget_codes(session_factory, document_id: str) -> None:
    """As if the document had been checked before codes were stored."""
    with session_factory.begin() as session:
        document = session.get(db.DocumentRow, document_id)
        for finding in document.findings:
            finding.text = None
        document.match.explanation_texts = None
        document.decision.reasons = [
            {**reason, "text": None} for reason in document.decision.reasons
        ]
        session.add(
            db.DeliveryRow(
                document_id=document_id,
                connector="erp",
                idempotency_key=f"{document_id}-erp",
                status="failed",
                booking={},
                attempts=1,
                next_attempt_at=datetime.now(UTC),
                last_error=str(Text("delivery.erp_unreachable", detail="connection refused")),
            )
        )


def _codes(session_factory, document_id: str) -> dict:
    with session_factory() as session:
        document = session.get(db.DocumentRow, document_id)
        delivery = session.query(db.DeliveryRow).filter_by(document_id=document_id).first()
        return {
            "findings": [f.text for f in document.findings],
            "explanation": document.match.explanation_texts,
            "reasons": [r.get("text") for r in document.decision.reasons],
            "delivery": None if delivery is None else delivery.error_text,
        }


def test_a_document_checked_before_codes_gets_them_back(session_factory):
    from app.checks.service import Checker
    from app.ingest import ingest
    from tests.support import PDF, FakeErp, inline_queue

    queue = inline_queue(session_factory, checker=Checker(FakeErp()))
    document_id = ingest(session_factory, queue, PDF, "r.pdf").document_id
    stored = _codes(session_factory, document_id)
    assert stored["findings"]
    assert all(stored["findings"])
    _forget_codes(session_factory, document_id)

    # Without --apply: counted, nothing written.
    report = backfill(session_factory, apply=False)
    assert report.coded["findings"] == len(stored["findings"])
    assert report.coded["delivery errors"] == 1
    assert not any(_codes(session_factory, document_id)["findings"])

    report = backfill(session_factory, apply=True)
    assert sum(report.english.values()) == 0, report.examples
    after = _codes(session_factory, document_id)
    assert after["findings"] == stored["findings"]
    assert after["explanation"] == stored["explanation"]
    assert after["reasons"] == stored["reasons"]
    assert after["delivery"] == Text("delivery.erp_unreachable", detail="connection refused").said()

    # Run again: nothing left to do.
    again = backfill(session_factory, apply=True)
    assert sum(again.coded.values()) == 0


def test_a_sentence_that_does_not_come_back_exactly_stays_english(session_factory):
    from app.checks.service import Checker
    from app.ingest import ingest
    from tests.support import PDF, FakeErp, inline_queue

    queue = inline_queue(session_factory, checker=Checker(FakeErp()))
    document_id = ingest(session_factory, queue, PDF, "r.pdf").document_id
    with session_factory.begin() as session:
        finding = session.get(db.DocumentRow, document_id).findings[0]
        finding.text, finding.message = None, "a wording no template has"
    report = backfill(session_factory, apply=True)
    assert report.english["findings"] == 1
    assert report.examples["findings"] == ["a wording no template has"]
    assert _codes(session_factory, document_id)["findings"][0] is None


def test_a_finding_that_is_an_order_note_gets_its_code(session_factory):
    from app.checks.service import Checker
    from app.ingest import ingest
    from tests.support import PDF, FakeErp, inline_queue

    queue = inline_queue(session_factory, checker=Checker(FakeErp()))
    document_id = ingest(session_factory, queue, PDF, "r.pdf").document_id
    note = Text("order_note.slip", po_ref="PO-309460", order="PO-349460")
    with session_factory.begin() as session:
        finding = session.get(db.DocumentRow, document_id).findings[0]
        finding.text, finding.message = None, str(note)
    backfill(session_factory, apply=True)
    assert _codes(session_factory, document_id)["findings"][0] == note.said()
