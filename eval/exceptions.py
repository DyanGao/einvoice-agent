"""Measure the checks on the exception set.

    python -m eval.exceptions [--split test] [--gate 0.95]

Builds the matching world and the exception set from it, puts the invoices
"on file" into an in-memory database and the world into an in-memory mock
ERP, and runs every case through the application's own checker: matching
first, then the rules. Per kind of planted problem it reports recall (was
the problem named?), precision (when that kind of finding was raised, was it
right?) and how many cases went to a person for any reason; for the clean
invoices, how many were sent to a person and why. No services, no model:
the cases are invoice data, not files, so reading errors are not part of
this measurement.
"""

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.checks.finding import Finding, FindingType, Severity
from app.checks.service import Checker
from app.db import Base, DocumentRow, DraftRow
from app.db.review import ReviewState
from app.extract.draft import InvoiceDraft
from app.models import DocumentFormat
from eval.generators.canonical import CanonicalInvoice
from eval.generators.exceptions import (
    CLEAN,
    DUPLICATE,
    IBAN,
    KINDS,
    MISSING_PO,
    PRICE,
    TOTALS,
    WRONG_VAT,
    ExceptionCase,
    ExceptionSet,
    build_exception_set,
)
from eval.generators.world import DEFAULT_SEED, build_world
from eval.match import erp_for

# Which findings count as naming each kind of planted problem.
DETECTED_BY: dict[str, frozenset[FindingType]] = {
    WRONG_VAT: frozenset({FindingType.VAT_RATE_IMPLAUSIBLE, FindingType.VAT_ID_INVALID}),
    TOTALS: frozenset({FindingType.TOTALS_MISMATCH}),
    DUPLICATE: frozenset(
        {
            FindingType.DUPLICATE,
            FindingType.NEAR_DUPLICATE,
            FindingType.DELIVERY_ALREADY_BILLED,
        }
    ),
    PRICE: frozenset({FindingType.PRICE_DEVIATION}),
    IBAN: frozenset({FindingType.IBAN_CHANGED}),
    MISSING_PO: frozenset({FindingType.MISSING_PO, FindingType.PO_NOT_FOUND}),
}
# The kinds whose recall the gate looks at (EVAL_PLAN: at least 95 %).
GATED = (IBAN, DUPLICATE)


@dataclass(frozen=True)
class Outcome:
    case: ExceptionCase
    findings: tuple[Finding, ...]

    def review_types(self) -> set[FindingType]:
        """Only findings that send the invoice to a person count: one shown
        for information stops nothing."""
        return {f.type for f in self.findings if f.severity is Severity.REVIEW}

    def flagged(self) -> bool:
        return bool(self.review_types())

    def detected(self) -> bool:
        """The planted problem was named (never true for a clean invoice)."""
        return bool(self.review_types() & DETECTED_BY.get(self.case.kind, frozenset()))


def _document(key: str, invoice: CanonicalInvoice) -> DocumentRow:
    """A stored document as the pipeline leaves a valid e-invoice."""
    draft = InvoiceDraft.model_validate(
        invoice.to_gold(DocumentFormat.UNKNOWN).model_dump()
    ).model_dump(mode="json")
    return DocumentRow(
        sha256=hashlib.sha256(key.encode()).hexdigest(),
        filename=f"{key}.xml",
        format=DocumentFormat.UNKNOWN.value,
        status="valid",
        content=b"",
        review_state=ReviewState.NOT_REQUIRED.value,
        draft=DraftRow(original=draft, data=draft),
    )


def run(exceptions: ExceptionSet) -> list[Outcome]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    checker = Checker(erp_for(exceptions.world))
    outcomes = []
    with Session(engine) as session:
        # The invoices on file went through the same processing when they arrived.
        on_file = [_document(entry.id, entry.invoice) for entry in exceptions.on_file]
        session.add_all(on_file)
        session.flush()
        for document in on_file:
            checker.check(session, document)
        session.commit()
        for case in exceptions.cases:
            document = _document(case.id, case.invoice)
            session.add(document)
            session.flush()
            outcomes.append(Outcome(case, tuple(checker.check(session, document))))
            # Each case meets the same invoices on file, never another case.
            session.rollback()
    engine.dispose()
    return outcomes


def _share(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(part / whole, 4)


def score(outcomes: list[Outcome]) -> dict:
    by_kind = {}
    for kind in KINDS:
        members = [outcome for outcome in outcomes if outcome.case.kind == kind]
        detected = sum(outcome.detected() for outcome in members)
        # The same finding raised on an invoice that has no such problem.
        false_alarms = sum(
            bool(outcome.review_types() & DETECTED_BY[kind])
            for outcome in outcomes
            if outcome.case.kind != kind
        )
        recall = _share(detected, len(members))
        precision = _share(detected, detected + false_alarms)
        by_kind[kind] = {
            "cases": len(members),
            "detected": detected,
            "false_alarms": false_alarms,
            "recall": recall,
            "precision": precision,
            "f1": None
            if not recall or not precision
            else round(2 * precision * recall / (precision + recall), 4),
            "sent_to_review": sum(outcome.flagged() for outcome in members),
        }
    variants: dict[tuple[str, str], list[Outcome]] = {}
    for outcome in outcomes:
        variants.setdefault((outcome.case.kind, outcome.case.variant), []).append(outcome)
    clean = [outcome for outcome in outcomes if outcome.case.kind == CLEAN]
    return {
        "by_kind": by_kind,
        "by_variant": {
            f"{kind}/{variant}": {
                "cases": len(members),
                "detected": sum(outcome.detected() for outcome in members),
                "sent_to_review": sum(outcome.flagged() for outcome in members),
            }
            for (kind, variant), members in sorted(variants.items())
        },
        "clean": {
            "cases": len(clean),
            "flagged": sum(outcome.flagged() for outcome in clean),
            "by_finding": dict(
                sorted(
                    Counter(
                        kind.value for outcome in clean for kind in outcome.review_types()
                    ).items()
                )
            ),
        },
        "missed": [
            {
                "id": outcome.case.id,
                "kind": outcome.case.kind,
                "variant": outcome.case.variant,
                "findings": sorted(kind.value for kind in outcome.review_types()),
            }
            for outcome in outcomes
            if outcome.case.kind != CLEAN and not outcome.detected()
        ],
        "clean_flagged": [
            {
                "id": outcome.case.id,
                "variant": outcome.case.variant,
                "findings": [f.message for f in outcome.findings if f.severity is Severity.REVIEW],
            }
            for outcome in clean
            if outcome.flagged()
        ],
    }


def _percent(value: float | None) -> str:
    return f"{'-':>10}" if value is None else f"{value * 100:>9.1f}%"


def _print(result: dict) -> None:
    print(
        f"{'kind':<14}{'cases':>6}{'recall':>10}{'precision':>10}{'F1':>10}"
        f"{'false alarms':>14}{'to review':>11}"
    )
    for kind, row in result["by_kind"].items():
        print(
            f"{kind:<14}{row['cases']:>6}{_percent(row['recall'])}{_percent(row['precision'])}"
            f"{_percent(row['f1'])}{row['false_alarms']:>14}{row['sent_to_review']:>11}"
        )
    clean = result["clean"]
    print(
        f"clean invoices: {clean['cases']}, sent to review: {clean['flagged']}"
        + (f" ({clean['by_finding']})" if clean["flagged"] else "")
    )
    print(f"{'variant':<40}{'cases':>6}{'detected':>10}{'to review':>11}")
    for name, row in result["by_variant"].items():
        print(f"  {name:<38}{row['cases']:>6}{row['detected']:>10}{row['sent_to_review']:>11}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=("dev", "test", "all"), default="test")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path("eval/out/exceptions"))
    parser.add_argument(
        "--gate",
        type=float,
        help=f"fail unless recall for {' and '.join(GATED)} is at least this",
    )
    args = parser.parse_args()
    outcomes = [
        outcome
        for outcome in run(build_exception_set(build_world(args.seed), args.seed))
        if args.split in ("all", outcome.case.split)
    ]
    result = score(outcomes)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"checks-{args.split}.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(f"== exception set, {args.split} split, seed {args.seed}")
    _print(result)
    failed = [
        kind
        for kind in GATED
        if args.gate is not None and (result["by_kind"][kind]["recall"] or 0) < args.gate
    ]
    if failed:
        print(f"GATE FAILED: recall below {args.gate} for {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
