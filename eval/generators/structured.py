"""Generate the synthetic structured set: every invoice as XRechnung UBL,
XRechnung CII and a ZUGFeRD-style PDF, plus one gold record per file.

    python -m eval.generators.structured --count 200 --out eval/out/structured
"""

import argparse
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from app.models import DocumentFormat, Invoice
from eval.generators.canonical import DEFECTS, EN16931, XRECHNUNG, Defect, build_invoice
from eval.generators.xrechnung import render_cii, render_ubl
from eval.generators.zugferd import render_zugferd_pdf

DEFAULT_SEED = 20261111
# Every fifth invoice carries a deliberate rule violation.
DEFECT_EVERY = 5
GOLD_FILE = "gold.jsonl"


@dataclass(frozen=True)
class GeneratedDocument:
    doc_id: str
    filename: str
    content: bytes
    format: DocumentFormat
    profile: str
    defect: str | None
    expected_valid: bool
    expected_rule: str | None
    invoice: Invoice

    def gold_record(self) -> dict:
        return {
            "id": self.doc_id,
            "file": self.filename,
            "format": self.format.value,
            "profile": self.profile,
            "defect": self.defect,
            "expected_valid": self.expected_valid,
            "expected_rule": self.expected_rule,
            "invoice": self.invoice.model_dump(mode="json"),
        }


def _defect_for(index: int) -> Defect | None:
    if index % DEFECT_EVERY != DEFECT_EVERY - 1:
        return None
    return DEFECTS[(index // DEFECT_EVERY) % len(DEFECTS)]


def generate(count: int, seed: int = DEFAULT_SEED) -> Iterator[GeneratedDocument]:
    for index in range(count):
        defect = _defect_for(index)
        canonical = build_invoice(seed, index)
        if defect is not None:
            canonical = defect.apply(canonical)
        # ZUGFeRD carries plain EN 16931 and XRechnung profiles in turn.
        pdf_profile = EN16931 if index % 2 == 0 else XRECHNUNG
        cii = render_cii(canonical, XRECHNUNG)
        renderings = (
            ("ubl", "xml", DocumentFormat.UBL, XRECHNUNG, render_ubl(canonical, XRECHNUNG)),
            ("cii", "xml", DocumentFormat.CII, XRECHNUNG, cii),
            (
                "zugferd",
                "pdf",
                DocumentFormat.ZUGFERD_PDF,
                pdf_profile,
                render_zugferd_pdf(canonical, render_cii(canonical, pdf_profile)),
            ),
        )
        for suffix, extension, fmt, profile, content in renderings:
            violates = defect is not None and defect.applies_to(profile)
            doc_id = f"inv-{index:04d}-{suffix}"
            yield GeneratedDocument(
                doc_id=doc_id,
                filename=f"{doc_id}.{extension}",
                content=content,
                format=fmt,
                profile=profile,
                defect=defect.name if defect else None,
                expected_valid=not violates,
                expected_rule=defect.rule_id if violates else None,
                invoice=canonical.to_gold(fmt),
            )


def write_set(out_dir: Path, count: int, seed: int = DEFAULT_SEED) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    with (out_dir / GOLD_FILE).open("w", encoding="utf-8") as gold:
        for document in generate(count, seed):
            (out_dir / document.filename).write_bytes(document.content)
            gold.write(json.dumps(document.gold_record(), ensure_ascii=False, sort_keys=True))
            gold.write("\n")
            written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=200, help="number of invoices (×3 files)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path("eval/out/structured"))
    args = parser.parse_args()
    print(f"wrote {write_set(args.out, args.count, args.seed)} documents to {args.out}")


if __name__ == "__main__":
    main()
