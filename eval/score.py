"""Score predictions against gold.

    python -m eval.score eval/out/structured --gate

Field accuracy: every modelled field of every document is one comparison
(line items and VAT groups field by field). A field counts as correct when the
values are equal after loading both sides into the Invoice model, so "1.5" and
"1.50" are the same amount. Fields the prediction lacks, or adds, are wrong.

Validation correctness: the validator's verdict equals the expected one, and
for an expected rejection the expected rule is among the reported errors. It
only applies to documents whose gold carries an expected verdict (structured
formats); PDFs and scans have none.

Predictions may be partial (an InvoiceDraft): a missing field is a wrong field.
"""

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.extract import InvoiceDraft
from app.models import Invoice
from eval.generators.structured import GOLD_FILE
from eval.run import predictions_file, read_jsonl

NOT_SCORED = {"field_confidence", "derived_fields", "unread_fields"}
_MISSING = object()


def flatten(invoice: Invoice | InvoiceDraft) -> dict[str, Any]:
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


def wrong_fields(gold: Invoice, predicted: Invoice | InvoiceDraft | None) -> tuple[list[str], int]:
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
    validation_expected: int = 0
    validation_correct: int = 0

    @property
    def field_accuracy(self) -> float:
        return self.fields_correct / self.fields_total if self.fields_total else 0.0

    @property
    def validation_correctness(self) -> float | None:
        if not self.validation_expected:
            return None
        return self.validation_correct / self.validation_expected

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
    # How often each field was wrong; line and VAT-group positions are folded together.
    wrong_by_field: Counter = field(default_factory=Counter)

    @property
    def gate_passed(self) -> bool:
        """Stage 1 gate: the structured path must be perfect."""
        return bool(self.by_format) and all(
            bucket.fields_correct == bucket.fields_total
            and bucket.validation_correct == bucket.validation_expected
            for bucket in self.by_format.values()
        )

    def to_json(self) -> dict:
        return {
            "gate_passed": self.gate_passed,
            "by_format": {name: bucket.summary() for name, bucket in self.by_format.items()},
            "wrong_by_field": dict(self.wrong_by_field.most_common()),
            "failures": self.failures,
        }


_POSITION = re.compile(r"\.\d+\.")


def score(gold_records: list[dict], predictions: list[dict], split: str | None = None) -> Report:
    by_id = {prediction["id"]: prediction for prediction in predictions}
    report = Report()
    for gold in gold_records:
        if split is not None and gold.get("split") != split:
            continue
        bucket = report.by_format.setdefault(gold["format"], Bucket())
        prediction = by_id.get(gold["id"]) or {"valid": None, "error_rules": [], "invoice": None}
        predicted = prediction["invoice"]
        wrong, compared = wrong_fields(
            Invoice.model_validate(gold["invoice"]),
            None if predicted is None else InvoiceDraft.model_validate(predicted),
        )
        has_verdict = gold["expected_valid"] is not None
        verdict_ok = validation_correct(gold, prediction) if has_verdict else True

        bucket.documents += 1
        bucket.fields_total += compared
        bucket.fields_correct += compared - len(wrong)
        bucket.documents_exact += not wrong
        bucket.validation_expected += has_verdict
        bucket.validation_correct += has_verdict and verdict_ok
        report.wrong_by_field.update(_POSITION.sub(".*.", path) for path in wrong)
        if wrong or not verdict_ok:
            report.failures.append(
                {
                    "id": gold["id"],
                    "wrong_fields": wrong,
                    "validation_correct": verdict_ok,
                    "expected_valid": gold["expected_valid"],
                    "expected_rule": gold["expected_rule"],
                    "predicted_valid": prediction.get("valid"),
                    "predicted_rules": prediction.get("error_rules", []),
                    "error": prediction.get("error"),
                }
            )
    return report


def format_table(report: Report) -> str:
    rows = ["format        docs   field acc.   doc exact   validation"]
    for name, bucket in sorted(report.by_format.items()):
        summary = bucket.summary()
        validation = summary["validation_correctness"]
        rows.append(
            f"{name:<12} {bucket.documents:>5}   {summary['field_accuracy']:>9.2%}   "
            f"{summary['document_exact_match']:>9.2%}   "
            + ("      n/a" if validation is None else f"{validation:>9.2%}")
        )
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("set_dir", type=Path)
    parser.add_argument("--gate", action="store_true", help="exit 1 unless everything is correct")
    parser.add_argument("--split", choices=("dev", "test"), help="score only this split")
    parser.add_argument("--system", default="structured", help="which predictions file to score")
    args = parser.parse_args()

    report = score(
        read_jsonl(args.set_dir / GOLD_FILE),
        read_jsonl(args.set_dir / predictions_file(args.system)),
        split=args.split,
    )
    (args.set_dir / f"report-{args.system}.json").write_text(
        json.dumps(report.to_json(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(format_table(report))
    for path, count in report.wrong_by_field.most_common(12):
        print(f"  wrong {count:>4}x  {path}")
    for failure in report.failures[:5]:
        print("FAIL", json.dumps(failure, ensure_ascii=False))
    if args.gate and not report.gate_passed:
        print("gate FAILED: structured path must reach 100% field accuracy and validation")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
