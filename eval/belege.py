"""Reading accuracy on a third-party set: the Belege sample (Immineal, 2026).

40 synthetic German invoices and credit notes as images (clean, scanned,
photographed), with labels in their own schema. They come from a generator
that is not ours, so they test whether reading holds outside our own data.
Licensed for evaluation, not for redistribution: the files, the recorded
model answers (which repeat the documents' contents) and the predictions stay
outside git, under data/private/ by default. Only aggregate numbers are
published.

    uv run --env-file .env python -m eval.belege ~/Downloads/belege-sample-40 \\
        --model claude-haiku-5-5 --llm-mode record

Only what the labels hold is scored, field by field:

- number, issue and due date, document type (Rechnung 380, Gutschrift 381),
  currency, seller and buyer name, the VAT ID (the seller's), the IBAN;
- net, VAT and gross total, the number of lines, and each line's quantity,
  unit price and net amount.

Credit notes print every amount with a minus sign; an XRechnung credit note
(type 381) carries the same amounts unsigned. Amounts are therefore compared
by their absolute value, so either convention counts as read correctly.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.extract import CompletingExtractor, ExtractionError, InvoiceDraft
from eval.run import build_llm_extractor

DEFAULT_OUT = Path("data/private/belege")
TYPE_CODES = {"Rechnung": "380", "Gutschrift": "381"}


def _date(text: str) -> str:
    return datetime.strptime(text, "%d.%m.%Y").date().isoformat()


def _amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    return abs(Decimal(str(value))).quantize(Decimal("0.01"))


def _compact(text: str | None) -> str | None:
    return None if text is None else " ".join(text.split()).casefold()


def _code(text: str | None) -> str | None:
    return None if text is None else "".join(text.split()).upper()


def expected(label: dict) -> dict[str, Any]:
    """The comparable values a Belege label holds, keyed like our fields."""
    fields = {name: field["text"] for name, field in label["fields"].items()}
    values: dict[str, Any] = {
        "number": fields["invoice_number"],
        "issue_date": _date(fields["invoice_date"]),
        "type_code": TYPE_CODES[label["document_type"]],
        "currency": label["currency"],
        "seller.name": _compact(fields["supplier_name"]),
        "buyer.name": _compact(fields["customer_name"]),
        "payment_iban": _code(fields["iban"]),
        "net_total": _amount(label["totals"]["net"]),
        "vat_total": _amount(label["totals"]["vat"]),
        "gross_total": _amount(label["totals"]["gross"]),
        "line_count": len(label["line_items"]),
    }
    if "due_date" in fields:
        values["due_date"] = _date(fields["due_date"])
    if "vat_id" in fields:
        values["seller.vat_id"] = _code(fields["vat_id"])
    for position, line in enumerate(label["line_items"]):
        values[f"lines.{position}.quantity"] = _amount(line["quantity"]["value"])
        values[f"lines.{position}.unit_price"] = _amount(line["unit_price"]["value"])
        values[f"lines.{position}.net"] = _amount(line["line_total"]["value"])
    return values


def read(draft: InvoiceDraft | None, wanted: dict[str, Any]) -> dict[str, Any]:
    """The same values from what the model read; a field it left out is None."""
    if draft is None:
        return dict.fromkeys(wanted)
    ibans = {_code(iban) for iban in draft.payment_ibans}
    values: dict[str, Any] = {
        "number": draft.number,
        "issue_date": draft.issue_date and draft.issue_date.isoformat(),
        "due_date": draft.due_date and draft.due_date.isoformat(),
        "type_code": draft.type_code,
        "currency": draft.currency,
        "seller.name": _compact(draft.seller.name),
        "buyer.name": _compact(draft.buyer.name),
        "seller.vat_id": _code(draft.seller.vat_id),
        # Read when the printed IBAN is among those the model found.
        "payment_iban": wanted.get("payment_iban") if wanted.get("payment_iban") in ibans else None,
        "net_total": _amount(draft.net_total),
        "vat_total": _amount(draft.vat_total),
        "gross_total": _amount(draft.gross_total),
        "line_count": len(draft.lines),
    }
    for position, line in enumerate(draft.lines):
        values[f"lines.{position}.quantity"] = _amount(line.quantity)
        values[f"lines.{position}.unit_price"] = _amount(line.unit_price)
        values[f"lines.{position}.net"] = _amount(line.net)
    return {name: values.get(name) for name in wanted}


def wrong(label: dict, draft: InvoiceDraft | None) -> tuple[list[str], int]:
    """The labelled fields read wrong, and how many were compared."""
    wanted = expected(label)
    got = read(draft, wanted)
    return [name for name, value in wanted.items() if got[name] != value], len(wanted)


def field_group(name: str) -> str:
    return "lines" if name.startswith("lines.") else name


def summarize(results: list[dict]) -> dict:
    """Field accuracy overall and by capture condition, VAT case and field."""
    overall = Counter()
    by: dict[str, Counter] = defaultdict(Counter)
    wrong_by_field = Counter()
    for result in results:
        compared, misses = result["compared"], len(result["wrong"])
        overall.update(fields=compared, wrong=misses, documents=1, exact=int(misses == 0))
        for key in (f"variant:{result['variant']}", f"vat_scheme:{result['vat_scheme']}"):
            by[key].update(fields=compared, wrong=misses, documents=1, exact=int(misses == 0))
        wrong_by_field.update(field_group(name) for name in result["wrong"])

    def accuracy(counts: Counter) -> dict:
        return {
            "documents": counts["documents"],
            "fields": counts["fields"],
            "field_accuracy": 1 - counts["wrong"] / counts["fields"] if counts["fields"] else None,
            "document_exact_match": counts["exact"] / counts["documents"]
            if counts["documents"]
            else None,
        }

    return {
        "overall": accuracy(overall),
        "by": {key: accuracy(counts) for key, counts in sorted(by.items())},
        "wrong_by_field": dict(wrong_by_field.most_common()),
        "errors": sum(1 for result in results if result["error"]),
    }


def evaluate(dataset: Path, extractor, rules: bool) -> list[dict]:
    if rules:
        extractor = CompletingExtractor(extractor)
    results = []
    for entry in (json.loads(line) for line in (dataset / "manifest.jsonl").open()):
        label = json.loads((dataset / entry["label"]).read_text(encoding="utf-8"))
        try:
            draft, error = extractor.extract((dataset / entry["image"]).read_bytes()), None
        except ExtractionError as exc:
            draft, error = None, str(exc)
        misses, compared = wrong(label, draft)
        results.append(
            {
                "id": entry["id"],
                "variant": entry["variant"],
                "layout": entry["layout"],
                "vat_scheme": entry["vat_scheme"],
                "compared": compared,
                "wrong": misses,
                "error": error,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset", type=Path, help="the unpacked belege-sample-40 folder")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort")
    parser.add_argument("--llm-mode", choices=("replay", "record"), default="replay")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    recordings = args.out / "recordings"
    summary = {"model": args.model, "effort": args.effort}
    for system, rules in (("llm", False), ("llm-rules", True)):
        # The rules only complete what the model read: the second pass replays.
        mode = args.llm_mode if not rules else "replay"
        extractor = build_llm_extractor(mode, recordings, args.model, args.effort)
        results = evaluate(args.dataset, extractor, rules)
        details = args.out / f"results-{args.model}-{system}.jsonl"
        details.parent.mkdir(parents=True, exist_ok=True)
        details.write_text("".join(json.dumps(r) + "\n" for r in results), encoding="utf-8")
        summary[system] = summarize(results)
    (args.out / f"summary-{args.model}.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
