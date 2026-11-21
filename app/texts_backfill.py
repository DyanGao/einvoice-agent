"""Codes for texts stored before they had any (v1.0 Stage 4).

Since increment 2 every text the server writes for people is stored with its
code and parameters next to the English sentence, so the review UI can show
it in German. Rows from before have the sentence only, and the UI shows them
in English. This finds the code back from the sentence: each English template
in `app/texts.py` is matched against it, and a code is kept only when the
`Text` made from it is exactly the stored sentence again. Whatever does not
come back exactly (a wording that has changed since, a sentence of no
template) stays as it is, in English.

Filled in: findings (`findings.text`), match explanations
(`matches.explanation_texts`), decision reasons (`text` in each reason) and
delivery errors (`deliveries.error_text`). Not the history (`audit_events`):
it records what happened, and is not rewritten afterwards.

    einvoice texts-backfill           what would be filled in (nothing written)
    einvoice texts-backfill --apply   fill it in
"""

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import cache
from string import Formatter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import db
from app.db.rls import tenant_transaction
from app.texts import ENGLISH, Text

# Parameters that hold another text, and the codes it can have
# (app/checks/rules.py, app/matching/matcher.py, app/decide/engine.py).
NESTED: dict[str, tuple[str, ...]] = {
    "check": ("check.",),
    "party": ("party.",),
    "problem": ("vat_id.", "match."),
    "grounds": ("grounds.",),
    "note": ("match.transfer",),
}
# A list of texts joined by "; ": a decision's serious findings.
NESTED_LISTS: dict[str, tuple[str, ...]] = {"details": ("finding.",)}
# Except `problem`, which is the ERP's own message where the ERP did not answer,
# these always hold a text.
MAY_BE_PLAIN = frozenset({"problem"})

_FIELDS = Formatter()


def _template_parts(template: str) -> list[tuple[str, str | None]]:
    return [(literal, name or None) for literal, name, _, _ in _FIELDS.parse(template)]


_PARTS = {code: _template_parts(template) for code, template in ENGLISH.items()}


def _splits(sentence: str, parts: list[tuple[str, str | None]]) -> Iterator[dict[str, str]]:
    """Every way the sentence fits the template's fixed text: the values of its
    parameters. A parameter is never empty."""

    def walk(position: int, index: int, values: dict[str, str]) -> Iterator[dict[str, str]]:
        if index == len(parts):
            if position == len(sentence):
                yield dict(values)
            return
        literal, name = parts[index]
        if not sentence.startswith(literal, position):
            return
        start = position + len(literal)
        if name is None:
            yield from walk(start, index + 1, values)
            return
        if index + 1 == len(parts):
            stops: list[int] = [len(sentence)] if len(sentence) > start else []
        elif parts[index + 1][0] == "":
            # Two parameters side by side: any place between them.
            stops = list(range(start + 1, len(sentence) + 1))
        else:
            # Every place the next fixed text follows.
            following = parts[index + 1][0]
            stops, at = [], sentence.find(following, start + 1)
            while at != -1:
                stops.append(at)
                at = sentence.find(following, at + 1)
        for stop in stops:
            value = sentence[start:stop]
            if name in values and values[name] != value:
                continue
            yield from walk(stop, index + 1, {**values, name: value})

    yield from walk(0, 0, {})


def _percent(value: str) -> Decimal | None:
    """ "12.0%" → 0.12, the fraction the sentence was made from."""
    if not value.endswith("%"):
        return None
    try:
        fraction = Decimal(value[:-1]) / 100
    except InvalidOperation:
        return None
    return Decimal(format(fraction.normalize(), "f"))


def _inside(value: Any) -> int:
    """How many texts a value holds, itself included."""
    if isinstance(value, Text):
        return 1 + sum(_inside(param) for param in value.params.values())
    if isinstance(value, list):
        return sum(_inside(item) for item in value)
    return 0


def _texts(value: str, prefixes: tuple[str, ...]) -> list[Text] | None:
    """A "; "-joined list of texts, split where each piece is a text (pieces may
    contain "; " themselves). Of several ways, the one with the most texts in it."""
    pieces = value.split("; ")
    best: dict[int, list[Text]] = {0: []}
    for start in range(len(pieces)):
        if start not in best:
            continue
        for end in range(start + 1, len(pieces) + 1):
            text = parse("; ".join(pieces[start:end]), prefixes, strict=True)
            if text is None:
                continue
            candidate = [*best[start], text]
            if end not in best or _inside(candidate) > _inside(best[end]):
                best[end] = candidate
    return best.get(len(pieces))


def _value(name: str, raw: str) -> tuple[Any, int | None]:
    """A parameter's value from its text in the sentence, and how many texts
    were found inside it (more is better); None when it should hold a text
    and does not."""
    if name.endswith("_percent"):
        fraction = _percent(raw)
        return (raw, 0) if fraction is None else (fraction, 0)
    if name == "fields":
        return raw.split(", "), 0
    if name in NESTED_LISTS:
        texts = _texts(raw, NESTED_LISTS[name])
        return (raw, None) if texts is None else (texts, _inside(texts))
    if name in NESTED:
        text = parse(raw, NESTED[name], strict=True)
        if text is None:
            return raw, (0 if name in MAY_BE_PLAIN else None)
        return text, _inside(text)
    return raw, 0


@cache
def parse(sentence: str, prefixes: tuple[str, ...] = (), *, strict: bool = False) -> Text | None:
    """The Text that is exactly this sentence, or None. With `prefixes`, only
    codes starting with one of them. `strict` (for a text inside another):
    every parameter that holds a text must be one, so that a template made of
    little but parameters ("{note} {grounds}") does not take any words."""
    # The best fit: the most texts found inside, then the template with the most
    # fixed text (a "{detail}" fits anything and is the last choice).
    best: tuple[int, int, Text] | None = None
    for code, parts in _PARTS.items():
        if prefixes and not code.startswith(prefixes):
            continue
        for split in _splits(sentence, parts):
            params: dict[str, Any] = {}
            found = 0
            plain = False
            for name, raw in split.items():
                params[name], inside = _value(name, raw)
                if inside is None:
                    plain = True
                else:
                    found += inside
            if plain and strict:
                continue
            try:
                text = Text(code, **params)
            except (ValueError, KeyError, InvalidOperation):
                continue
            fixed = sum(len(literal) for literal, _ in parts)
            if str(text) == sentence and (best is None or (found, fixed) > best[:2]):
                best = (found, fixed, text)
    return None if best is None else best[2]


@dataclass
class Report:
    """What was (or would be) filled in, and how many stay English, per place."""

    coded: Counter = field(default_factory=Counter)
    english: Counter = field(default_factory=Counter)
    examples: dict[str, list[str]] = field(default_factory=dict)

    def left(self, place: str, sentence: str) -> None:
        self.english[place] += 1
        examples = self.examples.setdefault(place, [])
        if len(examples) < 5 and sentence not in examples:
            examples.append(sentence)


# Which codes may stand where.
# An order matched by content or with a slip is a finding whose message is its order note.
# Findings about a credit note's invoice say what the link says (credit.*).
FINDINGS = ("finding.", "order_note.", "credit.")
# A credit note's match explains the invoice it corrects (app/checks/credit.py).
MATCHES = ("match.", "order_note.", "credit.")
REASONS = ("reason.",)
DELIVERIES = ("delivery.",)


def _tenant(session: Session, report: Report, apply: bool) -> None:
    # A row without a code holds SQL NULL (from before the column) or JSON null:
    # both are None here, so the rows are picked in Python, not by the query.
    for finding in session.scalars(select(db.FindingRow)):
        if finding.text is not None:
            continue
        text = parse(finding.message, FINDINGS)
        if text is None:
            report.left("findings", finding.message)
            continue
        report.coded["findings"] += 1
        if apply:
            finding.text = text.said()

    for match in session.scalars(select(db.MatchRow)):
        texts = list(match.explanation_texts or [None] * len(match.explanation))
        changed = False
        for index, line in enumerate(match.explanation):
            if texts[index] is not None:
                continue
            text = parse(line, MATCHES)
            if text is None:
                report.left("match explanations", line)
                continue
            report.coded["match explanations"] += 1
            texts[index], changed = text.said(), True
        if apply and changed:
            match.explanation_texts = texts

    for decision in session.scalars(select(db.DecisionRow)):
        reasons = [dict(reason) for reason in decision.reasons]
        changed = False
        for reason in reasons:
            if reason.get("text") is not None or not reason.get("message"):
                continue
            text = parse(reason["message"], REASONS)
            if text is None:
                report.left("decision reasons", reason["message"])
                continue
            report.coded["decision reasons"] += 1
            reason["text"], changed = text.said(), True
        if apply and changed:
            decision.reasons = reasons

    for delivery in session.scalars(
        select(db.DeliveryRow).where(db.DeliveryRow.last_error.is_not(None))
    ):
        if delivery.error_text is not None:
            continue
        text = parse(delivery.last_error, DELIVERIES)
        if text is None:
            report.left("delivery errors", delivery.last_error)
            continue
        report.coded["delivery errors"] += 1
        if apply:
            delivery.error_text = text.said()


def backfill(session_factory: sessionmaker[Session], *, apply: bool) -> Report:
    """Find the codes of every tenant's old texts; write them only with `apply`.
    Each tenant in a transaction of its own, acting for it."""
    with session_factory() as session:
        tenant_ids = list(session.scalars(select(db.TenantRow.id)))
    report = Report()
    for tenant_id in tenant_ids:
        # Without `apply` nothing is changed, so the commit writes nothing.
        with tenant_transaction(session_factory, tenant_id) as session:
            _tenant(session, report, apply)
    return report
