"""Fit and check the confidence calibration.

    python -m eval.calibrate fit    eval/out/pdf eval/out/scans
    python -m eval.calibrate report eval/out/pdf eval/out/scans

`fit` measures, on the dev split, how often a field with a given kind of
evidence (app/extract/confidence.py) was right, and writes that table as the
calibration the application ships with. `report` scores the held-out split
with it: expected calibration error, a reliability table, and how well low
confidence finds the wrong fields.

Both read the predictions of a system (default "llm-rules") that eval.run
wrote before; nothing here calls a model.
"""

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from app.extract.confidence import (
    ASSUMED_FIELDS,
    DEFAULT_CALIBRATION,
    Calibration,
    assess,
    bucket,
    document_text,
    score,
)
from app.extract.draft import InvoiceDraft
from app.models import Invoice
from eval.generators.structured import GOLD_FILE
from eval.run import LLM_RULES, predictions_file, read_jsonl
from eval.score import flatten

VERSION = "2026-11-13.1"
BINS = 10
_MISSING = object()


@dataclass(frozen=True)
class FieldCase:
    """One extracted field of one document."""

    document: str
    path: str
    bucket: str
    confidence: float | None  # None while fitting
    correct: bool


def cases(
    set_dirs: list[Path], system: str, split: str, calibration: Calibration | None
) -> list[FieldCase]:
    found = []
    for set_dir in set_dirs:
        predictions = {p["id"]: p for p in read_jsonl(set_dir / predictions_file(system))}
        for gold in read_jsonl(set_dir / GOLD_FILE):
            prediction = predictions[gold["id"]]
            if gold.get("split") != split or prediction["invoice"] is None:
                continue
            draft = InvoiceDraft.model_validate(prediction["invoice"])
            text = document_text((set_dir / gold["file"]).read_bytes())
            truth = flatten(Invoice.model_validate(gold["invoice"]))
            read = flatten(draft)
            confidence = {} if calibration is None else score(draft, text, calibration)
            for path, evidence in assess(draft, text).items():
                if path in draft.derived_fields and path in ASSUMED_FIELDS:
                    continue  # scored by policy, not by the table (see confidence.py)
                found.append(
                    FieldCase(
                        document=gold["id"],
                        path=path,
                        bucket=bucket(draft.source_format, evidence),
                        confidence=confidence.get(path),
                        correct=truth.get(path, _MISSING) == read[path],
                    )
                )
    return found


def fit(field_cases: list[FieldCase]) -> dict:
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for case in field_cases:
        counts[case.bucket][0] += 1
        counts[case.bucket][1] += case.correct
    total = len(field_cases)
    right = sum(case.correct for case in field_cases)
    return {
        "version": VERSION,
        "fitted_on": {"split": "dev", "fields": total, "correct": right},
        # Laplace smoothing: no bucket claims certainty, and a bucket seen
        # twice is not trusted like one seen two thousand times.
        "buckets": {
            key: {"n": n, "correct": correct, "probability": round((correct + 1) / (n + 2), 4)}
            for key, (n, correct) in sorted(counts.items())
        },
        "fallback": 0.5,
    }


def expected_calibration_error(field_cases: list[FieldCase]) -> tuple[float, list[dict]]:
    bins: list[list[FieldCase]] = [[] for _ in range(BINS)]
    for case in field_cases:
        bins[min(int(case.confidence * BINS), BINS - 1)].append(case)
    rows, error = [], 0.0
    for index, members in enumerate(bins):
        if not members:
            continue
        mean_confidence = sum(case.confidence for case in members) / len(members)
        accuracy = sum(case.correct for case in members) / len(members)
        error += len(members) / len(field_cases) * abs(mean_confidence - accuracy)
        rows.append(
            {
                "bin": f"{index / BINS:.1f}-{(index + 1) / BINS:.1f}",
                "fields": len(members),
                "mean_confidence": round(mean_confidence, 4),
                "accuracy": round(accuracy, 4),
            }
        )
    return error, rows


def report(field_cases: list[FieldCase], threshold: float) -> dict:
    error, reliability = expected_calibration_error(field_cases)
    wrong = [case for case in field_cases if not case.correct]
    flagged = [case for case in field_cases if case.confidence < threshold]
    by_bucket: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for case in field_cases:
        by_bucket[case.bucket][0] += 1
        by_bucket[case.bucket][1] += case.correct
    return {
        "fields": len(field_cases),
        "wrong": len(wrong),
        "ece": round(error, 4),
        "reliability": reliability,
        # Accuracy per kind of evidence on this split: do the signals separate
        # right from wrong here, whatever the calibration table says?
        "buckets": {
            key: {"fields": n, "accuracy": round(correct / n, 4)}
            for key, (n, correct) in sorted(by_bucket.items())
        },
        "threshold": threshold,
        "flagged": len(flagged),
        "wrong_flagged": sum(case.confidence < threshold for case in wrong),
        "wrong_not_flagged": [
            {"document": case.document, "field": case.path, "confidence": case.confidence}
            for case in wrong
            if case.confidence >= threshold
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("fit", "report"))
    parser.add_argument("set_dirs", type=Path, nargs="+")
    parser.add_argument("--system", default=LLM_RULES)
    parser.add_argument("--split", choices=("dev", "test"), default="test")
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--threshold", type=float, default=0.8)
    args = parser.parse_args()
    if args.command == "fit":
        table = fit(cases(args.set_dirs, args.system, "dev", None))
        args.calibration.write_text(json.dumps(table, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(table, indent=1))
    else:
        calibration = Calibration.load(args.calibration)
        result = report(cases(args.set_dirs, args.system, args.split, calibration), args.threshold)
        print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
