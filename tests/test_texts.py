"""Texts the server writes for people (v1.0 Stage 4, increment 2): every
code has an English and a German text in the review UI, with the same
parameters, and the UI's English is the server's own."""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.texts import ENGLISH, Text, placeholders, said

MESSAGES = Path("ui/lib/messages.ts")
_ENTRY = re.compile(r'"server\.([^"]+)":\s*("(?:[^"\\]|\\.)*")')


def catalogues() -> tuple[dict[str, str], dict[str, str]]:
    source = MESSAGES.read_text(encoding="utf-8")
    english, german = source.split("export const de", 1)

    def entries(block: str) -> dict[str, str]:
        return {code: json.loads(value) for code, value in _ENTRY.findall(block)}

    return entries(english), entries(german)


def test_every_server_code_has_an_english_and_a_german_text():
    english, german = catalogues()
    assert set(english) == set(ENGLISH)
    assert set(german) == set(ENGLISH)


def test_the_uis_english_is_the_servers_own():
    english, _ = catalogues()
    different = {code for code in ENGLISH if english[code] != ENGLISH[code]}
    assert different == set()


def test_the_german_texts_have_the_same_parameters():
    _, german = catalogues()
    differing = {
        code: (sorted(placeholders(ENGLISH[code])), sorted(placeholders(german[code])))
        for code in ENGLISH
        if placeholders(ENGLISH[code]) != placeholders(german[code])
    }
    assert differing == {}


def test_every_code_the_application_names_exists():
    named = set()
    for path in Path("app").rglob("*.py"):
        named |= set(re.findall(r'\bText\(\s*"([a-z_.]+)"', path.read_text(encoding="utf-8")))
    assert named - set(ENGLISH) == set()
    # Codes made at run time from a value: every value has one.
    for code in ("party.seller", "party.buyer", "finding.price_deviation"):
        assert code in ENGLISH
    for basis in ("exact_reference", "similar_reference", "content"):
        assert f"grounds.{basis}" in ENGLISH


def test_a_text_is_its_english_sentence():
    text = Text("finding.duplicate", number="RE-1")
    assert text == "invoice RE-1 from this supplier is already on file"
    assert isinstance(text, str)


def test_a_text_is_stored_with_code_plain_parameters_and_english():
    text = Text(
        "finding.vat_id_invalid",
        party=Text("party.seller"),
        vat_id="DE1",
        problem=Text("vat_id.german_shape"),
    )
    assert said(text) == {
        "code": "finding.vat_id_invalid",
        "params": {
            "party": {"code": "party.seller", "params": {}, "text": "seller"},
            "vat_id": "DE1",
            "problem": {
                "code": "vat_id.german_shape",
                "params": {},
                "text": "a German VAT ID is DE followed by nine digits",
            },
        },
        "text": "seller VAT ID DE1: a German VAT ID is DE followed by nine digits",
    }


def test_amounts_and_shares_are_kept_exact_for_the_ui():
    text = Text(
        "finding.price_deviation",
        line=1,
        invoiced_amount=Decimal("1234.50"),
        above_percent=Decimal("0.1234"),
        ordered_amount=Decimal("1100"),
        order="PO-1",
    )
    assert text == "line 1: price 1234.50 is 12.3% above the 1100 on order PO-1"
    assert said(text)["params"]["invoiced_amount"] == "1234.50"


def test_a_plain_string_has_no_code():
    assert said("written before codes") is None


def test_a_text_with_the_wrong_parameters_is_refused():
    with pytest.raises(ValueError, match="finding.duplicate"):
        Text("finding.duplicate", number="RE-1", extra="x")


def test_a_checked_document_keeps_the_codes_of_its_findings_and_reasons(session_factory):
    from app import db
    from app.checks.service import Checker
    from app.ingest import ingest
    from tests.support import PDF, FakeErp, inline_queue

    queue = inline_queue(session_factory, checker=Checker(FakeErp()))
    document_id = ingest(session_factory, queue, PDF, "r.pdf").document_id
    with session_factory() as session:
        document = session.get(db.DocumentRow, document_id)
        finding_codes = {finding.text["code"] for finding in document.findings}
        reason_codes = {reason["text"]["code"] for reason in document.decision.reasons}
        explanation = document.match.explanation_texts
    assert "finding.unknown_supplier" in finding_codes
    assert "reason.findings" in reason_codes
    assert explanation == [said(Text("match.no_supplier"))]
