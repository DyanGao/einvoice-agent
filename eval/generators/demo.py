"""The five invoices of the README demo, written to sample_invoices/.

From the matching world, so the mock ERP knows their suppliers, orders and
deliveries: four ordinary invoices that name their order exactly, and one
of the same kind with a line priced 12 % above its order. All five are
e-invoices (XRechnung UBL and CII, ZUGFeRD), read by the parser and the
validator; no model is involved, so the demo needs no API key and gives the
same result every time. Everything is invented.

    python -m eval.generators.demo --out sample_invoices
"""

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from eval.generators.canonical import EN16931, XRECHNUNG, CanonicalInvoice
from eval.generators.exceptions import raise_prices
from eval.generators.world import PO_EXACT, World, WorldInvoice, build_world
from eval.generators.xrechnung import render_cii, render_ubl
from eval.generators.zugferd import render_zugferd_pdf

# The price rise of the flagged invoice (README: "price +12% vs PO").
PRICE_RISE_PERCENT = 12


def _ubl(invoice: CanonicalInvoice) -> bytes:
    return render_ubl(invoice, XRECHNUNG)


def _cii(invoice: CanonicalInvoice) -> bytes:
    return render_cii(invoice, XRECHNUNG)


def _zugferd(invoice: CanonicalInvoice) -> bytes:
    return render_zugferd_pdf(invoice, render_cii(invoice, EN16931))


# (format name, file suffix, renderer) for the five samples in order.
_FORMATS: tuple[tuple[str, str, Callable[[CanonicalInvoice], bytes]], ...] = (
    ("XRechnung UBL", "xml", _ubl),
    ("XRechnung CII", "xml", _cii),
    ("ZUGFeRD", "pdf", _zugferd),
    ("XRechnung UBL", "xml", _ubl),
    ("XRechnung CII", "xml", _cii),
)


@dataclass(frozen=True)
class Sample:
    filename: str
    format: str
    entry: WorldInvoice
    invoice: CanonicalInvoice
    # What the demo expects of it: booked without review, or flagged.
    flagged: bool

    def render(self) -> bytes:
        return next(render for name, _, render in _FORMATS if name == self.format)(self.invoice)


def _plain(world: World) -> list[WorldInvoice]:
    """Ordinary invoices: the order quoted exactly, a supplier with one
    account, each supplier once."""
    ibans = {supplier.id: supplier.ibans for supplier in world.suppliers}
    seen: set[str] = set()
    chosen = []
    for entry in world.invoices:
        if entry.po_ref_kind != PO_EXACT or len(ibans[entry.supplier_id]) != 1:
            continue
        if entry.supplier_id in seen:
            continue
        seen.add(entry.supplier_id)
        chosen.append(entry)
    return chosen


def build_samples(world: World) -> list[Sample]:
    entries = _plain(world)[: len(_FORMATS)]
    samples = []
    for position, (entry, (name, suffix, _)) in enumerate(zip(entries, _FORMATS, strict=True)):
        flagged = position == len(_FORMATS) - 1
        invoice = raise_prices(entry.invoice, {0}, PRICE_RISE_PERCENT) if flagged else entry.invoice
        number = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in invoice.number)
        samples.append(
            Sample(
                filename=f"{position + 1:02d}-{number}.{suffix}",
                format=name,
                entry=entry,
                invoice=invoice,
                flagged=flagged,
            )
        )
    return samples


def _readme(samples: list[Sample]) -> str:
    rows = "\n".join(
        f"| `{s.filename}` | {s.format} | {s.invoice.seller.name} | {s.invoice.po_ref} | "
        f"{s.invoice.gross_total} {s.invoice.currency} | "
        + (
            f"flagged: line 1 priced {PRICE_RISE_PERCENT} % above the order"
            if s.flagged
            else "booked without review"
        )
        + " |"
        for s in samples
    )
    return f"""# Sample invoices

The five invoices `make demo` processes. **All synthetic**: suppliers, orders, amounts and bank
accounts are invented by `eval/generators/world.py` (seed 20261113), not taken from real invoices.
Regenerate with `uv run python -m eval.generators.demo --out sample_invoices`.

| File | Format | Supplier | Order | Total | Expected |
|---|---|---|---|---|---|
{rows}
"""


def write_samples(samples: list[Sample], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for sample in samples:
        (out / sample.filename).write_bytes(sample.render())
    (out / "README.md").write_text(_readme(samples), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("sample_invoices"))
    args = parser.parse_args()
    samples = build_samples(build_world())
    write_samples(samples, args.out)
    print(f"wrote {len(samples)} sample invoices to {args.out}")


if __name__ == "__main__":
    main()
