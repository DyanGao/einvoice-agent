"""Measure credit notes end to end on the credit-note set (v1.0 Stage 5).

    python -m eval.credits [--split test] [--gate 0.95]

Builds the matching world and the credit-note set from it
(eval/generators/credits.py), puts the world's invoices "on file" into an
in-memory database and the world, with the refunds and offsets, into an
in-memory mock ERP. Each credit note is then read from its file — the XML by
the application's parsers, the PDFs by the rule-based reader followed by the
completion rules (the model's reading of credit notes is not measured here:
it needs new recordings) — and goes through the application's own checker
and booking:

- reading: field accuracy against what was generated, per format;
- link: the invoice it was linked to is the credited one, or none where none
  is right (an unknown reference, an invoice never received). A link to the
  wrong invoice is counted apart: worse than none, a person is not warned;
- payments: the refund or offset found is the one made, and for an offset,
  the invoice paid less the credit shows both parts;
- booking: what would be booked is a credit note (381) against the linked
  invoice, never an invoice; and no credit note is booked without a person.

Every case runs twice: as read from its file, and as read correctly (the
generated credit note itself), which measures the link and the payments
apart from reading. The rule-based reader is weak on the held-out layouts of
the test split, for invoices as much as for credit notes (docs/eval-report.md),
so the link through it there measures reading, not linking.

The gate: the link right for at least --gate of the cases read correctly; and
in both runs no credit note booked as an invoice and none without review.
"""

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.checks.service import Checker
from app.db import DEFAULT_TENANT_ID, Base, DocumentRow, DraftRow
from app.db.review import ReviewState
from app.delivery import NotBookable, booking_for
from app.extract import BaselineExtractor, CompletingExtractor, ExtractionError
from app.extract.confidence import document_text
from app.extract.draft import InvoiceDraft
from app.models import DocumentFormat
from app.models.invoice import CREDIT_NOTE, INVOICE
from app.parse.dispatch import parse_invoice
from app.pipeline import DocumentStatus
from eval.exceptions import stored_document
from eval.generators.credits import (
    CII,
    FORMATS,
    PDF,
    PDF_NEGATIVE,
    SETTLEMENTS,
    UBL,
    VARIANTS,
    CreditCase,
    CreditSet,
    ExpectedPayment,
    build_credit_set,
    write_set,
)
from eval.generators.world import DEFAULT_SEED, build_world
from eval.match import erp_for
from eval.score import wrong_fields

AS_READ, READ_CORRECTLY = "as_read", "read_correctly"
_SOURCE_FORMAT = {
    UBL: DocumentFormat.UBL,
    CII: DocumentFormat.CII,
    PDF: DocumentFormat.PDF_TEXT,
    PDF_NEGATIVE: DocumentFormat.PDF_TEXT,
}


@dataclass(frozen=True)
class Outcome:
    case: CreditCase
    # What was read; None when nothing could be.
    draft: InvoiceDraft | None
    wrong_fields: tuple[str, ...]
    compared: int
    linked_to: str | None
    payments: frozenset[ExpectedPayment]
    offset_payments: frozenset[ExpectedPayment]
    # The booking's document type and credited invoice; None when not bookable.
    booked_type: str | None
    booked_invoice: str | None
    route: str

    def link_right(self) -> bool:
        return self.linked_to == self.case.expected_invoice_id

    def link_wrong(self) -> bool:
        """Linked, and to another invoice than the credited one."""
        return self.linked_to is not None and not self.link_right()

    def payments_right(self) -> bool:
        return self.payments == frozenset(self.case.payments)

    def payments_wrong(self) -> bool:
        """A transfer was named, and not the one that settled it."""
        return bool(self.payments) and not self.payments_right()

    def booked_as_invoice(self) -> bool:
        return self.booked_type is not None and self.booked_type != CREDIT_NOTE


Reader = Callable[[CreditCase], InvoiceDraft | None]


def file_reader(pdf_reader=None) -> Reader:
    """The credit note as the application reads its file: the XML parsed, a
    PDF by `pdf_reader` (the rule-based reader and the completion rules)."""
    pdf_reader = pdf_reader or CompletingExtractor(BaselineExtractor(), text_of=document_text)

    def read(case: CreditCase) -> InvoiceDraft | None:
        if case.format in (UBL, CII):
            invoice = parse_invoice(case.content)
            return InvoiceDraft.model_validate(invoice.model_dump())
        try:
            return pdf_reader.extract(case.content)
        except ExtractionError:
            return None

    return read


def read_correctly(case: CreditCase) -> InvoiceDraft:
    """What a reader without errors gets: the checks, the link and the
    payments measured apart from reading."""
    return InvoiceDraft.model_validate(
        case.credit.to_gold(_SOURCE_FORMAT[case.format]).model_dump()
    )


def _read_document(case: CreditCase, draft: InvoiceDraft) -> DocumentRow:
    data = draft.model_dump(mode="json")
    structured = case.format in (UBL, CII)
    return DocumentRow(
        tenant_id=DEFAULT_TENANT_ID,
        sha256=hashlib.sha256(case.id.encode()).hexdigest(),
        filename=case.filename,
        format=_SOURCE_FORMAT[case.format].value,
        status=(DocumentStatus.VALID if structured else DocumentStatus.EXTRACTED).value,
        content=b"",
        review_state=ReviewState.PENDING.value,
        draft=DraftRow(original=data, data=data),
    )


def _payments(document: DocumentRow) -> frozenset[ExpectedPayment]:
    if document.match is None:
        return frozenset()
    return frozenset(
        ExpectedPayment(
            part["bank_transaction_id"], Decimal(part["amount"]), part.get("credit_note")
        )
        for part in document.match.payments
    )


def run(credit_set: CreditSet, read: Reader) -> list[Outcome]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    checker = Checker(erp_for(credit_set.world))
    outcomes = []
    with Session(engine) as session:
        on_file = {
            entry.id: stored_document(entry.id, entry.invoice) for entry in credit_set.on_file
        }
        session.add_all(on_file.values())
        session.flush()
        for document in on_file.values():
            checker.check(session, document)
        session.commit()
        document_of = {document.id: key for key, document in on_file.items()}
        for case in credit_set.cases:
            gold = case.credit.to_gold(_SOURCE_FORMAT[case.format])
            draft = read(case)
            wrong, compared = wrong_fields(gold, draft)
            if draft is None:
                outcomes.append(
                    Outcome(case, None, tuple(wrong), compared, None, frozenset(), frozenset(),
                            None, None, "review")
                )  # fmt: skip
                continue
            document = _read_document(case, draft)
            session.add(document)
            session.flush()
            checker.check(session, document)
            link = document.credit_link
            linked_to = (
                None
                if link is None or link.invoice_document_id is None
                else document_of[link.invoice_document_id]
            )
            try:
                booking = booking_for(document)
                booked_type, booked_invoice = booking.document_type, booking.preceding_invoice
            except (NotBookable, ValueError):
                booked_type = booked_invoice = None
            offset = frozenset()
            if case.offset_invoice_id is not None:
                # The invoice paid less the credit, looked at again now that
                # the credit note is on file.
                partner = on_file[case.offset_invoice_id]
                checker.check(session, partner)
                offset = _payments(partner)
            outcomes.append(
                Outcome(
                    case,
                    draft,
                    tuple(wrong),
                    compared,
                    linked_to,
                    _payments(document),
                    offset,
                    booked_type,
                    booked_invoice,
                    document.decision.route,
                )
            )
            # Each credit note meets the same invoices on file, never another one.
            session.rollback()
    engine.dispose()
    return outcomes


def _share(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(part / whole, 4)


def _group(outcomes: list[Outcome], key: Callable[[Outcome], str], keys) -> dict:
    groups = {name: [o for o in outcomes if key(o) == name] for name in keys}
    return {name: members for name, members in groups.items() if members}


def score(outcomes: list[Outcome], numbers: dict[str, str]) -> dict:
    """`numbers`: each world invoice's number, to check what a booking credits."""
    reading = {}
    for fmt, members in _group(outcomes, lambda o: o.case.format, FORMATS).items():
        compared = sum(o.compared for o in members)
        reading[fmt] = {
            "cases": len(members),
            "field_accuracy": _share(
                compared - sum(len(o.wrong_fields) for o in members), compared
            ),
            "exact": sum(not o.wrong_fields for o in members),
            "read_as_credit_note": sum(
                o.draft is not None and o.draft.type_code == CREDIT_NOTE for o in members
            ),
            "wrong_by_field": dict(
                Counter(
                    field.split(".")[0] if field.startswith("lines.") else field
                    for o in members
                    for field in o.wrong_fields
                ).most_common()
            ),
        }

    def link(members: list[Outcome]) -> dict:
        return {
            "cases": len(members),
            "right": sum(o.link_right() for o in members),
            "wrong": sum(o.link_wrong() for o in members),
            "accuracy": _share(sum(o.link_right() for o in members), len(members)),
        }

    def payments(members: list[Outcome]) -> dict:
        return {
            "cases": len(members),
            "right": sum(o.payments_right() for o in members),
            "wrong": sum(o.payments_wrong() for o in members),
        }

    offsets = [o for o in outcomes if o.case.offset_invoice_id is not None]
    booked = [o for o in outcomes if o.booked_type is not None]
    return {
        "cases": len(outcomes),
        "reading": reading,
        "link": link(outcomes),
        "link_by_variant": {
            name: link(members)
            for name, members in _group(outcomes, lambda o: o.case.variant, VARIANTS).items()
        },
        "link_by_format": {
            name: link(members)
            for name, members in _group(outcomes, lambda o: o.case.format, FORMATS).items()
        },
        "payments": payments(outcomes),
        "payments_by_settlement": {
            name: payments(members)
            for name, members in _group(outcomes, lambda o: o.case.settlement, SETTLEMENTS).items()
        },
        "offset_invoices": {
            "cases": len(offsets),
            "right": sum(o.offset_payments == frozenset(o.case.offset_payments) for o in offsets),
        },
        "booking": {
            "bookable": len(booked),
            "booked_as_invoice": sum(o.booked_as_invoice() for o in outcomes),
            # Read as an ordinary invoice: not booked as one only because
            # something else was missing. Not gated, but worth seeing.
            "read_as_invoice": sum(
                o.draft is not None and o.draft.type_code == INVOICE for o in outcomes
            ),
            # Linked: the booking credits that invoice.
            "credits_the_linked_invoice": sum(
                o.booked_invoice == numbers[o.linked_to] for o in booked if o.linked_to
            ),
            "linked": sum(1 for o in booked if o.linked_to),
            "booked_without_review": sum(o.route == "auto" for o in outcomes),
        },
        "wrong_links": [
            {
                "id": o.case.id,
                "variant": o.case.variant,
                "format": o.case.format,
                "expected": o.case.expected_invoice_id,
                "linked_to": o.linked_to,
            }
            for o in outcomes
            if not o.link_right()
        ],
        "wrong_payments": [
            {
                "id": o.case.id,
                "settlement": o.case.settlement,
                "format": o.case.format,
                "expected": sorted(str(p) for p in o.case.payments),
                "found": sorted(str(p) for p in o.payments),
            }
            for o in outcomes
            if not o.payments_right()
        ],
    }


def _percent(value: float | None) -> str:
    return f"{'-':>9}" if value is None else f"{value * 100:>8.1f}%"


def _print(result: dict) -> None:
    if result["reading"]:
        print(f"{'reading':<14}{'cases':>6}{'fields':>9}{'exact':>7}{'as 381':>8}")
    for fmt, row in result["reading"].items():
        print(
            f"  {fmt:<12}{row['cases']:>6}{_percent(row['field_accuracy'])}"
            f"{row['exact']:>7}{row['read_as_credit_note']:>8}"
        )
    print(f"{'link':<26}{'cases':>6}{'right':>7}{'wrong':>7}{'accuracy':>10}")
    rows = {"all": result["link"]} | result["link_by_variant"] | result["link_by_format"]
    for name, row in rows.items():
        print(
            f"  {name:<24}{row['cases']:>6}{row['right']:>7}{row['wrong']:>7}"
            f"{_percent(row['accuracy'])}"
        )
    print(f"{'payments':<26}{'cases':>6}{'right':>7}{'wrong':>7}")
    rows = {"all": result["payments"]} | result["payments_by_settlement"]
    for name, row in rows.items():
        print(f"  {name:<24}{row['cases']:>6}{row['right']:>7}{row['wrong']:>7}")
    offsets = result["offset_invoices"]
    print(f"  invoices paid less a credit: {offsets['right']} of {offsets['cases']} right")
    booking = result["booking"]
    print(
        f"booking: {booking['bookable']} bookable, {booking['booked_as_invoice']} as an invoice "
        f"({booking['read_as_invoice']} read as one), "
        f"{booking['credits_the_linked_invoice']} of {booking['linked']} linked credit the "
        f"linked invoice; {booking['booked_without_review']} booked without review"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=("dev", "test", "all"), default="test")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path("eval/out/credits"))
    parser.add_argument("--gate", type=float, help="fail unless the link is right this often")
    args = parser.parse_args()
    world = build_world(args.seed)
    credit_set = build_credit_set(world, args.seed)
    write_set(credit_set, args.out)
    numbers = {entry.id: entry.invoice.number for entry in world.invoices}
    result = {}
    for name, reader in ((AS_READ, file_reader()), (READ_CORRECTLY, read_correctly)):
        outcomes = [o for o in run(credit_set, reader) if args.split in ("all", o.case.split)]
        result[name] = score(outcomes, numbers)
        if name == READ_CORRECTLY:
            result[name]["reading"] = {}  # nothing was read
    (args.out / f"credits-{args.split}.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    for name, title in ((AS_READ, "as read from the files"), (READ_CORRECTLY, "read correctly")):
        print(f"== credit-note set, {args.split} split, seed {args.seed}: {title}")
        _print(result[name])
    if args.gate is None:
        return 0
    failed = []
    link = result[READ_CORRECTLY]["link"]["accuracy"] or 0
    if link < args.gate:
        failed.append(f"link right {link} < {args.gate} (read correctly)")
    for name, scored in result.items():
        booking = scored["booking"]
        if booking["booked_as_invoice"]:
            failed.append(f"{booking['booked_as_invoice']} booked as invoices ({name})")
        if booking["booked_without_review"]:
            failed.append(f"{booking['booked_without_review']} booked without review ({name})")
    if failed:
        print(f"GATE FAILED: {'; '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
