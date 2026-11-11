"""Score predictions against gold.

    python -m eval.score eval/out/structured --gate

Field accuracy: every modelled field of every document is one comparison
(line items and VAT groups field by field). A field counts as correct when the
values are equal after loading both sides into the Invoice model, so "1.5" and
"1.50" are the same amount. Fields the prediction lacks, or adds, are wrong.

Validation correctness: the validator's verdict equals the expected one, and
for an expected rejection the expected rule is among the reported errors.
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.models import Invoice
from eval.generators.structured import GOLD_FILE
from eval.run import PREDICTIONS_FILE, read_jsonl

REPORT_FILE = "report.json"
NOT_SCORED = {"field_confidence"}
_MISSING = object()


def flatten(invoice: Invoice) -> dict[str, Any]:
    flat: dict[str, Any] = {}

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(f"{prefix}.{key}" if prefix else key, child)
        elif isinstance(value, tuple | list) and value and isinstance(value[0], dict):
            for position, child in enumerate(value):
                walk(f"{prefix}.{position}", child)
        else:
            flat[prefix] = value

    walk("", invoice.model_dump(exclude=NOT_SCORED))
    return flat


def wrong_fields(gold: Invoice, predicted: Invoice | None) -> tuple[list[str], int]:
    """Paths that differ, and the number of comparisons made."""
    gold_flat = flatten(gold)
    predicted_flat = {} if predicted is None else flatten(predicted)
    paths = sorted(gold_flat.keys() | predicted_flat.keys())
    wrong = [
        path
        for path in paths
        if gold_flat.get(path, _MISSING) != predicted_flat.get(path, _MISSING)
    ]
    return wrong, len(paths)


def validation_correct(gold: dict, prediction: dict) -> bool:
    if prediction["valid"] is None or prediction["valid"] != gold["expected_valid"]:
        return False
    return gold["expected_rule"] is None or gold["expected_rule"] in prediction["error_rules"]


@dataclass
class Bucket:
    documents: int = 0
    fields_total: int = 0
    fields_correct: int = 0
    documents_exact: int = 0
    validation_correct: int = 0

    @property
    def field_accuracy(self) -> float:
        return self.fields_correct / self.fields_total if self.fields_total else 0.0

    @property
    def validation_correctness(self) -> float:
        return self.validation_correct / self.documents if self.documents else 0.0

    def summary(self) -> dict:
        return {
            **asdict(self),
            "field_accuracy": self.field_accuracy,
            "document_exact_match": self.documents_exact / self.documents if self.documents else 0,
            "validation_correctness": self.validation_correctness,
        }


@dataclass
class Report:
    by_format: dict[str, Bucket] = field(default_factory=dict)
    failures: list[dict] = field(default_factory=list)

    @property
    def gate_passed(self) -> bool:
        """Stage 1 gate: the structured path must be perfect."""
        return bool(self.by_format) and all(
            bucket.fields_correct == bucket.fields_total
            and bucket.validation_correct == bucket.documents
            for bucket in self.by_format.values()
        )

    def to_json(self) -> dict:
        return {
            "gate_passed": self.gate_passed,
            "by_format": {name: bucket.summary() for name, bucket in self.by_format.items()},
            "failures": self.failures,
        }


def score(gold_records: list[dict], predictions: list[dict]) -> Report:
    by_id = {prediction["id"]: prediction for prediction in predictions}
    report = Report()
    for gold in gold_records:
        bucket = report.by_format.setdefault(gold["format"], Bucket())
        prediction = by_id.get(gold["id"]) or {"valid": None, "error_rules": [], "invoice": None}
        predicted = prediction["invoice"]
        wrong, compared = wrong_fields(
            Invoice.model_validate(gold["invoice"]),
            None if predicted is None else Invoice.model_validate(predicted),
        )
        verdict_ok = validation_correct(gold, prediction)

        bucket.documents += 1
        bucket.fields_total += compared
        bucket.fields_correct += compared - len(wrong)
        bucket.documents_exact += not wrong
        bucket.validation_correct += verdict_ok
        if wrong or not verdict_ok:
            report.failures.append(
                {
                    "id": gold["id"],
                    "wrong_fields": wrong,
                    "validation_correct": verdict_ok,
                    "expected_valid": gold["expected_valid"],
                    "expected_rule": gold["expected_rule"],
                    "predicted_valid": prediction["valid"],
                    "predicted_rules": prediction["error_rules"],
                    "error": prediction.get("error"),
                }
            )
    return report


def format_table(report: Report) -> str:
    rows = ["format        docs   field acc.   doc exact   validation"]
    for name, bucket in sorted(report.by_format.items()):
        summary = bucket.summary()
        rows.append(
            f"{name:<12} {bucket.documents:>5}   {summary['field_accuracy']:>9.2%}   "
            f"{summary['document_exact_match']:>9.2%}   {summary['validation_correctness']:>9.2%}"
        )
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("set_dir", type=Path)
    parser.add_argument("--gate", action="store_true", help="exit 1 unless everything is correct")
    args = parser.parse_args()

    report = score(
        read_jsonl(args.set_dir / GOLD_FILE), read_jsonl(args.set_dir / PREDICTIONS_FILE)
    )
    (args.set_dir / REPORT_FILE).write_text(
        json.dumps(report.to_json(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(format_table(report))
    for failure in report.failures[:10]:
        print("FAIL", json.dumps(failure, ensure_ascii=False))
    if args.gate and not report.gate_passed:
        print("gate FAILED: structured path must reach 100% field accuracy and validation")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
