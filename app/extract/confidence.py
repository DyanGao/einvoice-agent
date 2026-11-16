"""How much to trust each extracted field.

The confidence is computed from evidence, never asked from the model: a model
that misreads a digit is just as sure of the wrong value. Two kinds of
evidence are collected per field:

- a check: a rule the value can pass or fail on its own or against the other
  fields (IBAN checksum, VAT ID shape, quantity x price = net, net + VAT = gross);
- grounding: whether the value can be found in the document's text (the PDF's
  text layer, or OCR for a scan).

A calibration table then turns each combination into the share of fields with
that evidence that were right on the dev set (eval/calibrate.py). Missing
fields get no confidence; they are shown as missing, not as uncertain.
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

from app.checks import arithmetic
from app.detect import detect
from app.extract.base import Extractor
from app.extract.draft import InvoiceDraft
from app.extract.ocr import OcrError, OcrUnavailable, ocr_text
from app.extract.pdf_text import layout_text
from app.models import DocumentFormat

logger = logging.getLogger(__name__)

DEFAULT_CALIBRATION = Path(__file__).with_name("calibration.json")
# A value that was assumed rather than read or computed (see complete.py). This
# is a policy, not a measurement: on synthetic data the assumption is always
# right, which says nothing about real invoices.
ASSUMED_FIELDS = frozenset({"currency"})
ASSUMED_CONFIDENCE = 0.5


class Check(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NONE = "none"  # no rule applies, or the rule's other inputs are missing


class Ground(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"  # no document text, or the value is not printed as such


@dataclass(frozen=True)
class Evidence:
    check: Check
    ground: Ground


def bucket(source_format: DocumentFormat | None, evidence: Evidence) -> str:
    """The calibration key. The format is part of it because "not found in the
    text" means something else for a text layer than for OCR of a scan."""
    fmt = "unknown" if source_format is None else source_format.value
    return f"{fmt}/{evidence.check.value}/{evidence.ground.value}"


# --- checks ---------------------------------------------------------------

_VAT_ID_SHAPE = re.compile(r"[A-Z]{2}[A-Z0-9]{2,12}")
_GERMAN_VAT_ID = re.compile(r"DE\d{9}")
_IBAN_SHAPE = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}")


def valid_iban(iban: str) -> bool:
    if not _IBAN_SHAPE.fullmatch(iban):
        return False
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 == 1


def _valid_vat_id(vat_id: str) -> bool:
    if vat_id.startswith("DE"):
        return bool(_GERMAN_VAT_ID.fullmatch(vat_id))
    return bool(_VAT_ID_SHAPE.fullmatch(vat_id)) and any(ch.isdigit() for ch in vat_id)


def _verdict(holds: bool) -> Check:
    return Check.PASSED if holds else Check.FAILED


def _checks(draft: InvoiceDraft) -> dict[str, Check]:
    checks: dict[str, Check] = {}

    if draft.payment_ibans:
        checks["payment_ibans"] = _verdict(all(map(valid_iban, draft.payment_ibans)))
    for role, party in (("seller", draft.seller), ("buyer", draft.buyer)):
        if party.vat_id is not None:
            checks[f"{role}.vat_id"] = _verdict(_valid_vat_id(party.vat_id))
    if draft.issue_date is not None and draft.due_date is not None:
        checks["due_date"] = _verdict(draft.due_date >= draft.issue_date)

    # The invoice's own arithmetic: every field a rule covers shares its verdict.
    for result in arithmetic.evaluate(draft):
        for path in result.fields:
            checks[path] = _verdict(result.holds)
    return checks


# --- grounding ------------------------------------------------------------

_SPACE = re.compile(r"\s+")
_PLAIN_NUMBER = re.compile(r"\d+(?:[.,']\d+)*")
_SPACED_NUMBER = re.compile(r"\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?")
_CURRENCY_SIGNS = {"EUR": "€", "USD": "$", "GBP": "£"}
# First three letters, German and English, are enough to tell the months apart.
_MONTHS = (
    ("jan",), ("feb",), ("mär", "mar"), ("apr",), ("mai", "may"), ("jun",),
    ("jul",), ("aug",), ("sep",), ("okt", "oct"), ("nov",), ("dez", "dec"),
)  # fmt: skip


class _Text:
    """The document's text, prepared once for the lookups below."""

    def __init__(self, text: str) -> None:
        self.raw = text
        self.folded = _SPACE.sub(" ", text).casefold()
        self.compact = _SPACE.sub("", text).upper()
        self.numbers = _numbers_in(text)


def _readings(token: str) -> list[Decimal]:
    """What a printed number can mean: "1.234" is 1.234 or 1234."""
    digits_only = re.sub(r"\D", "", token)
    readings = [digits_only]
    last = max(token.rfind("."), token.rfind(","))
    if last != -1:
        whole = re.sub(r"\D", "", token[:last])
        readings.append(f"{whole or '0'}.{token[last + 1 :]}")
    values = []
    for reading in readings:
        try:
            values.append(Decimal(reading))
        except InvalidOperation:
            continue
    return values


def _numbers_in(text: str) -> set[Decimal]:
    tokens = _PLAIN_NUMBER.findall(text) + _SPACED_NUMBER.findall(text)
    return {value for token in tokens for value in _readings(token)}


def _date_printed(value: date, text: _Text) -> bool:
    day, month, year = value.day, value.month, value.year
    years = f"(?:{year}|{year % 100:02d})"
    numeric = (
        rf"(?<!\d)0?{day}[./-]0?{month}[./-]{years}(?!\d)",
        rf"(?<!\d)0?{month}/0?{day}/{years}(?!\d)",
        rf"(?<!\d){year}-{month:02d}-{day:02d}(?!\d)",
    )
    names = "|".join(_MONTHS[month - 1])
    named = (
        rf"(?<!\d)0?{day}\.?\s*(?:{names})\w*\.?,?\s*{years}(?!\d)",
        rf"(?:{names})\w*\.?\s*0?{day}(?:st|nd|rd|th)?,?\s*{years}(?!\d)",
    )
    return any(re.search(pattern, text.folded) for pattern in numeric + named)


def _found(value: object, path: str, text: _Text) -> Ground:
    leaf = path.rsplit(".", 1)[-1]
    if leaf in ("type_code", "category"):
        return Ground.UNKNOWN  # codes we assign; invoices do not print them
    if leaf == "payment_ibans":
        found = all(iban in text.compact for iban in value)
    elif leaf == "vat_id":
        found = value in text.compact
    elif leaf == "currency":
        sign = _CURRENCY_SIGNS.get(value)
        found = value.casefold() in text.folded or (sign is not None and sign in text.raw)
    elif isinstance(value, Decimal):
        found = value in text.numbers
    elif isinstance(value, date):
        found = _date_printed(value, text)
    else:
        found = _SPACE.sub(" ", str(value)).casefold() in text.folded
    return Ground.FOUND if found else Ground.NOT_FOUND


# --- evidence and score ---------------------------------------------------


def _fields(draft: InvoiceDraft) -> dict[str, object]:
    """Every field that has a value, by the same dotted path a correction uses."""
    fields: dict[str, object] = {}
    for name in (
        "number", "issue_date", "type_code", "currency", "due_date", "buyer_reference",
        "po_ref", "line_net_total", "net_total", "vat_total", "gross_total", "amount_due",
    ):  # fmt: skip
        fields[name] = getattr(draft, name)
    fields["payment_ibans"] = draft.payment_ibans or None
    for role, party in (("seller", draft.seller), ("buyer", draft.buyer)):
        fields[f"{role}.name"] = party.name
        fields[f"{role}.vat_id"] = party.vat_id
    for table, rows in (("lines", draft.lines), ("vat_breakdown", draft.vat_breakdown)):
        for index, row in enumerate(rows):
            for name, value in row:
                fields[f"{table}.{index}.{name}"] = value
    return {path: value for path, value in fields.items() if value is not None}


def assess(draft: InvoiceDraft, text: str | None) -> dict[str, Evidence]:
    """The evidence for every field of the draft that has a value. `text` is
    the document's text, or None if there is none to compare with."""
    checks = _checks(draft)
    prepared = _Text(text) if text and text.strip() else None
    evidence = {}
    for path, value in _fields(draft).items():
        if prepared is None or path in draft.derived_fields:
            ground = Ground.UNKNOWN  # a filled-in value is not printed anywhere
        else:
            ground = _found(value, path, prepared)
        evidence[path] = Evidence(checks.get(path, Check.NONE), ground)
    return evidence


@dataclass(frozen=True)
class Calibration:
    """Share of correct fields per evidence bucket, measured on the dev set."""

    version: str
    probabilities: dict[str, float]
    # For evidence that never occurred on the dev set: nothing is known, so the
    # field is neither trusted nor written off.
    fallback: float

    @classmethod
    def load(cls, path: Path = DEFAULT_CALIBRATION) -> "Calibration":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            version=data["version"],
            probabilities={key: entry["probability"] for key, entry in data["buckets"].items()},
            fallback=data["fallback"],
        )


def score(draft: InvoiceDraft, text: str | None, calibration: Calibration) -> dict[str, float]:
    confidence = {}
    for path, evidence in assess(draft, text).items():
        if path in draft.derived_fields and path in ASSUMED_FIELDS:
            confidence[path] = ASSUMED_CONFIDENCE
        else:
            key = bucket(draft.source_format, evidence)
            confidence[path] = calibration.probabilities.get(key, calibration.fallback)
    return confidence


def document_text(data: bytes) -> str | None:
    """Text to ground values in: the text layer of a PDF, OCR for a scan.
    None if neither is available; confidence then rests on the checks alone."""
    if detect(data) is DocumentFormat.PDF_TEXT:
        return layout_text(data) or None
    try:
        return ocr_text(data) or None
    except (OcrUnavailable, OcrError) as exc:
        logger.warning("no OCR text to ground the extraction in: %s", exc)
        return None


class LastDocumentText:
    """`document_text` for the document seen last, kept so that two steps
    reading the same document (the completion rules, then scoring) run OCR
    once."""

    def __init__(self, text_of: Callable[[bytes], str | None] = document_text) -> None:
        self._text_of = text_of
        self._last: tuple[bytes, str | None] | None = None

    def __call__(self, data: bytes) -> str | None:
        if self._last is None or self._last[0] != data:
            self._last = (data, self._text_of(data))
        return self._last[1]


class ScoringExtractor:
    """An extractor whose drafts carry a confidence per field."""

    def __init__(
        self,
        inner: Extractor,
        calibration: Calibration,
        text_of: Callable[[bytes], str | None] = document_text,
    ) -> None:
        self._inner = inner
        self._calibration = calibration
        self._text_of = text_of

    def extract(self, data: bytes) -> InvoiceDraft:
        draft = self._inner.extract(data)
        text = self._text_of(data)
        return draft.model_copy(
            update={
                "field_confidence": score(draft, text, self._calibration),
                "unread_fields": printed_but_unread(draft, text),
            }
        )


# --- values printed in a standard format but not read ------------------------
# A field the reading left out has no confidence to be low. For fields with a
# standard format and check digits, the page itself can say that something
# was there: a valid Leitweg-ID, IBAN or German VAT ID in the document's text
# that is not among the values read. Formats, not labels: labels differ per
# supplier, the formats do not.

_LEITWEG = re.compile(r"(?<![\w-])(\d{2,12})(?:-([0-9A-Z]{1,30}))?-(\d{2})(?![\w-])")
_IBAN_IN_TEXT = re.compile(
    r"(?<![A-Z0-9])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?(?![A-Z0-9])"
)
_GERMAN_VAT_IN_TEXT = re.compile(r"(?<![A-Z0-9])DE[ ]?\d{9}(?!\d)")


def _mod97(digits_and_letters: str) -> int:
    return int("".join(str(int(char, 36)) for char in digits_and_letters)) % 97


def _is_date(token: str) -> bool:
    try:
        date.fromisoformat(token)
    except ValueError:
        return False
    return True


def _leitweg_ids(text: str) -> set[str]:
    found = set()
    for match in _LEITWEG.finditer(text):
        coarse, fine, check = match.groups()
        token = match.group(0)
        if not _is_date(token) and _mod97(coarse + (fine or "") + check) == 1:
            found.add(token)
    return found


def _ibans(text: str) -> set[str]:
    return {
        compact
        for match in _IBAN_IN_TEXT.finditer(text)
        if valid_iban(compact := match.group(0).replace(" ", ""))
    }


def printed_but_unread(draft: InvoiceDraft, text: str | None) -> tuple[str, ...]:
    """Fields whose value is on the page in a standard format but was not read."""
    if not text:
        return ()
    unread = []
    if draft.buyer_reference is None and _leitweg_ids(text):
        unread.append("buyer_reference")
    read_ibans = {iban.replace(" ", "") for iban in draft.payment_ibans}
    if _ibans(text) - read_ibans:
        unread.append("payment_ibans")
    seller, buyer = (
        draft.seller.vat_id if draft.seller else None,
        (draft.buyer.vat_id if draft.buyer else None),
    )
    printed_vat = {m.group(0).replace(" ", "") for m in _GERMAN_VAT_IN_TEXT.finditer(text)}
    if printed_vat - {seller, buyer}:
        unread.append("seller.vat_id" if seller is None else "buyer.vat_id")
    return tuple(unread)
