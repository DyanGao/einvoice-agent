"""Choose the confidence threshold of the decision engine from data.

    python -m eval.sweep fit    eval/out/pdf eval/out/scans
    python -m eval.sweep report eval/out/pdf eval/out/scans [--system baseline]

For every candidate threshold: how many invoices the reading part of the
decision engine (app/decide: missing fields, assumed values, confidence)
would let through without a person, and how many of those have a wrong
field. That is the auto-rate vs. error curve of EVAL_PLAN.

`fit` picks the operating point on the dev split — loosening the threshold
only while the invoices each step adds stay within the error target (1 %),
and of thresholds that let the same invoices through, the strictest — and
writes it to
app/decide/thresholds.json, the file the application loads. `report` shows
the curve on both splits and the operating point on the held-out split.

Measured here is reading only. Whether the checks catch planted problems is
measured on the exception set (eval/exceptions.py); the business rules of the
engine are policy and have no threshold. Reads predictions eval.run wrote
before; nothing here calls a model.
"""

import argparse
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.decide import THRESHOLDS_FILE, DecisionSettings, reading_causes
from app.extract.confidence import Calibration, document_text, score
from app.extract.draft import InvoiceDraft
from app.models import Invoice
from eval.generators.structured import GOLD_FILE
from eval.run import LLM_RULES, predictions_file, read_jsonl
from eval.score import wrong_fields

VERSION = "2026-11-14.1"
TARGET_ERROR = 0.01
# Regression warning (EVAL_PLAN): auto-rate this far below the reference.
WARN_DROP = 0.05


@dataclass(frozen=True)
class Document:
    set_name: str
    id: str
    split: str
    # None: the extractor gave nothing usable.
    draft: InvoiceDraft | None
    wrong: tuple[str, ...]

    def causes(self, threshold: float) -> list[str]:
        """Why the reading part of the decision engine would hold it back."""
        if self.draft is None:
            return ["nothing_read"]
        settings = DecisionSettings(min_field_confidence=threshold)
        return [reason.cause.value for reason in reading_causes(self.draft, settings)]

    def auto(self, threshold: float) -> bool:
        return not self.causes(threshold)


def documents(set_dirs: list[Path], system: str, calibration: Calibration) -> list[Document]:
    """Every document of the sets, scored with confidence as the application does."""
    found = []
    for set_dir in set_dirs:
        predictions = {p["id"]: p for p in read_jsonl(set_dir / predictions_file(system))}
        for gold in read_jsonl(set_dir / GOLD_FILE):
            prediction = predictions[gold["id"]]
            truth = Invoice.model_validate(gold["invoice"])
            draft = None
            if prediction["invoice"] is not None:
                read = InvoiceDraft.model_validate(prediction["invoice"])
                text = document_text((set_dir / gold["file"]).read_bytes())
                draft = read.model_copy(update={"field_confidence": score(read, text, calibration)})
            wrong, _ = wrong_fields(truth, draft)
            found.append(Document(set_dir.name, gold["id"], gold["split"], draft, tuple(wrong)))
    return found


def upper_bound(errors: int, n: int, level: float = 0.95) -> float | None:
    """One-sided Clopper-Pearson upper bound of the error rate: with
    `errors` wrong out of `n`, the true rate is below this with the given
    confidence. With no error in n invoices it is about 3/n."""
    if n == 0:
        return None
    if errors >= n:
        return 1.0

    def at_most(p: float) -> float:
        return sum(math.comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(errors + 1))

    low, high = errors / n, 1.0
    for _ in range(60):
        middle = (low + high) / 2
        low, high = (middle, high) if at_most(middle) > 1 - level else (low, middle)
    return round(high, 4)


def point(docs: list[Document], threshold: float) -> dict:
    through = [doc for doc in docs if doc.auto(threshold)]
    errors = sum(bool(doc.wrong) for doc in through)
    return {
        "threshold": threshold,
        "documents": len(docs),
        "auto": len(through),
        "auto_rate": round(len(through) / len(docs), 4) if docs else None,
        "errors": errors,
        "error_rate": round(errors / len(through), 4) if through else 0.0,
        "error_upper_95": upper_bound(errors, len(through)),
    }


def curve(docs: list[Document]) -> list[dict]:
    """One point per distinct confidence in the documents, plus 0 (only the
    other reading rules apply) — between two of them nothing changes."""
    values = {
        confidence
        for doc in docs
        if doc.draft is not None
        for confidence in doc.draft.field_confidence.values()
    }
    return [point(docs, threshold) for threshold in sorted(values | {0.0})]


def operating_point(points: list[dict], target: float = TARGET_ERROR) -> dict:
    """Loosen the threshold step by step, from the strictest, as long as the
    invoices each step lets through are wrong no more often than the target.

    Not simply "the most invoices within the target overall": that would
    spend the error budget on letting in an invoice known to be wrong, as
    long as enough right ones came before it.
    """
    chosen = None
    for p in sorted(points, key=lambda p: p["threshold"], reverse=True):
        if chosen is None:
            if p["error_rate"] > target:
                raise ValueError(f"even the strictest threshold has {p['error_rate']:.1%} errors")
            chosen = p
            continue
        added = p["auto"] - chosen["auto"]
        if added == 0:
            continue  # lets nothing more through: keep the stricter one
        if (p["errors"] - chosen["errors"]) / added > target or p["error_rate"] > target:
            break
        chosen = p
    if chosen is None:
        raise ValueError("no thresholds to choose from")
    return chosen


def _split(docs: list[Document], split: str, set_name: str | None = None) -> list[Document]:
    return [d for d in docs if d.split == split and set_name in (None, d.set_name)]


def fit(set_dirs: list[Path], system: str) -> dict:
    docs = documents(set_dirs, system, Calibration.load())
    chosen = operating_point(curve(_split(docs, "dev")))
    threshold = chosen["threshold"]
    return {
        "version": VERSION,
        "min_field_confidence": threshold,
        "fitted_on": {
            "sets": sorted({doc.set_name for doc in docs}),
            "system": system,
            "split": "dev",
            "target_error": TARGET_ERROR,
            "calibration": Calibration.load().version,
        },
        "dev": chosen,
        # Not used for choosing: what CI compares against to notice a drop.
        "reference_auto_rate": {
            name: point(_split(docs, "test", name), threshold)["auto_rate"]
            for name in sorted({doc.set_name for doc in docs})
        },
    }


def _row(p: dict) -> str:
    upper = "-" if p["error_upper_95"] is None else f"{p['error_upper_95'] * 100:.1f}%"
    return (
        f"  {p['threshold']:>9.4f}{p['auto']:>6}/{p['documents']:<5}{p['auto_rate'] * 100:>8.1f}%"
        f"{p['errors']:>8}{p['error_rate'] * 100:>9.2f}%{upper:>11}"
    )


def report(set_dirs: list[Path], system: str, settings: dict) -> tuple[dict, list[str]]:
    docs = documents(set_dirs, system, Calibration.load())
    threshold = settings["min_field_confidence"]
    result = {"system": system, "threshold": threshold, "version": settings["version"]}
    warnings = []
    for split in ("dev", "test"):
        members = _split(docs, split)
        result[split] = {
            "curve": curve(members),
            "operating_point": point(members, threshold),
            "by_set": {
                name: point(_split(docs, split, name), threshold)
                for name in sorted({d.set_name for d in members})
            },
            # Why the rest was not let through (a document can have several).
            "held_back": dict(
                sorted(Counter(cause for d in members for cause in d.causes(threshold)).items())
            ),
            "wrong_but_auto": [
                {"set": d.set_name, "id": d.id, "wrong": list(d.wrong)}
                for d in members
                if d.wrong and d.auto(threshold)
            ],
        }
    for name, reference in settings.get("reference_auto_rate", {}).items():
        found = result["test"]["by_set"].get(name)
        if system == LLM_RULES and found and found["auto_rate"] < reference - WARN_DROP:
            warnings.append(
                f"auto-rate on {name} (test) is {found['auto_rate']:.1%}, "
                f"more than {WARN_DROP:.0%} below the {reference:.1%} it had when fitted"
            )
    return result, warnings


def _print(result: dict) -> None:
    for split in ("dev", "test"):
        section = result[split]
        print(f"== {result['system']}, {split} split: auto-rate vs. error")
        print(
            f"  {'threshold':>9}{'auto':>12}{'rate':>9}{'errors':>8}{'error':>10}{'upper 95%':>11}"
        )
        for p in section["curve"]:
            print(_row(p))
        print(f"  operating point {result['threshold']} (thresholds {result['version']}):")
        print(_row(section["operating_point"]))
        for name, p in section["by_set"].items():
            print(f"  {name}:")
            print(_row(p))
        print(f"  held back, by reason: {section['held_back']}")
        for wrong in section["wrong_but_auto"]:
            print(f"  WRONG BUT AUTO {wrong}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("fit", "report"))
    parser.add_argument("sets", nargs="+", type=Path)
    parser.add_argument("--system", default=LLM_RULES)
    args = parser.parse_args()
    if args.command == "fit":
        fitted = fit(args.sets, args.system)
        THRESHOLDS_FILE.write_text(json.dumps(fitted, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {THRESHOLDS_FILE}: min_field_confidence {fitted['min_field_confidence']}")
        print(json.dumps(fitted["dev"], indent=1))
        return 0
    settings = json.loads(THRESHOLDS_FILE.read_text(encoding="utf-8"))
    result, warnings = report(args.sets, args.system, settings)
    out = args.sets[0].parent / f"decision-{args.system}.json"
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    _print(result)
    for warning in warnings:
        # A warning, not a failure (EVAL_PLAN): fewer invoices booked without
        # a person costs time, not correctness.
        print(f"::warning::{warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
