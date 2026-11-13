"""Measure matching against the world's ground truth.

    python -m eval.match [--split test] [--gate 0.95]

Builds the matching world, loads it into an in-memory mock ERP and lets the
matcher find each invoice's order, delivery and payments through the same
read API the application uses. Compared with the ground truth, per part and
per hard case, next to the exact-key baseline. No services, no model.
"""

import argparse
import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.erp import HttpErpClient
from app.extract.draft import InvoiceDraft
from app.matching import Match, PeerInvoice, match_by_exact_keys, match_invoice
from app.models import DocumentFormat
from eval.generators.world import DEFAULT_SEED, World, WorldInvoice, build_world
from mock_erp import seed
from mock_erp.main import create_app

MATCHER = "matcher"
BASELINE = "baseline"
PARTS = ("order", "delivery", "payments", "all")


@dataclass(frozen=True)
class Case:
    entry: WorldInvoice
    match: Match

    def correct(self) -> dict[str, bool]:
        truth = {(p.bank_transaction_id, p.amount) for p in self.entry.payments}
        found = {(p.bank_transaction_id, p.amount) for p in self.match.payments}
        parts = {
            "order": self.match.po_number == self.entry.po_number,
            "delivery": self.match.delivery_id == self.entry.delivery_id,
            "payments": found == truth,
        }
        return parts | {"all": all(parts.values())}

    def wrong_claims(self) -> list[str]:
        """Parts where the matcher named something and it was the wrong thing.
        Worse than finding nothing: a person is not asked, a wrong link is made."""
        correct = self.correct()
        named = {
            "order": self.match.po_number is not None,
            "delivery": self.match.delivery_id is not None,
            "payments": bool(self.match.payments),
        }
        return [part for part, was_named in named.items() if was_named and not correct[part]]


def erp_for(world: World) -> HttpErpClient:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    seed.load(world, engine)
    return HttpErpClient(TestClient(create_app(engine)))


def run(world: World, system: str, split: str | None) -> list[Case]:
    erp = erp_for(world)
    # The supplier's other invoices, as the application would have them on
    # file — taken from the invoices themselves, not from the ground truth.
    by_seller: dict[str | None, list[WorldInvoice]] = defaultdict(list)
    for entry in world.invoices:
        by_seller[entry.invoice.seller.vat_id].append(entry)
    cases = []
    for entry in world.invoices:
        if split is not None and entry.split != split:
            continue
        draft = InvoiceDraft.model_validate(
            entry.invoice.to_gold(DocumentFormat.UNKNOWN).model_dump()
        )
        if system == BASELINE:
            match = match_by_exact_keys(draft, erp)
        else:
            peers = [
                PeerInvoice(other.invoice.number, other.invoice.gross_total)
                for other in by_seller[entry.invoice.seller.vat_id]
                if other is not entry
            ]
            match = match_invoice(draft, erp, peers)
        cases.append(Case(entry, match))
    return cases


def accuracy(cases: list[Case], group: Callable[[Case], str] | None = None) -> dict:
    """Share of correct cases per part, overall or per group."""
    buckets: dict[str, list[Case]] = defaultdict(list)
    for case in cases:
        buckets["all invoices" if group is None else group(case)].append(case)
    return {
        name: {"invoices": len(members)}
        | {
            part: round(sum(case.correct()[part] for case in members) / len(members), 4)
            for part in PARTS
        }
        for name, members in sorted(buckets.items())
    }


def report(cases: list[Case]) -> dict:
    return {
        "overall": accuracy(cases),
        # Of the misses: how many named a wrong target instead of leaving it open.
        "wrong_claims": sum(bool(case.wrong_claims()) for case in cases),
        "left_open": sum(not case.correct()["all"] and not case.wrong_claims() for case in cases),
        "by_order_reference": accuracy(cases, lambda case: case.entry.po_ref_kind),
        "by_payment": accuracy(cases, lambda case: case.entry.payment_kind),
        "by_payment_reference": accuracy(cases, lambda case: str(case.entry.payment_ref_kind)),
        "wrong": [
            {
                "id": case.entry.id,
                "order_reference": case.entry.po_ref_kind,
                "payment": case.entry.payment_kind,
                "payment_reference": case.entry.payment_ref_kind,
                "wrong_parts": [p for p in PARTS[:3] if not case.correct()[p]],
                "wrong_claims": case.wrong_claims(),
                "explanation": list(case.match.explanation),
            }
            for case in cases
            if not case.correct()["all"]
        ],
    }


def _table(title: str, rows: dict) -> str:
    lines = [f"{title:<28}{'invoices':>9}" + "".join(f"{part:>10}" for part in PARTS)]
    for name, row in rows.items():
        lines.append(
            f"  {name:<26}{row['invoices']:>9}"
            + "".join(f"{row[part] * 100:>9.1f}%" for part in PARTS)
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--system", choices=(MATCHER, BASELINE), default=MATCHER)
    parser.add_argument("--split", choices=("dev", "test"), default="test")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path("eval/out/world"))
    parser.add_argument(
        "--gate", type=float, help="fail unless this share of invoices is matched fully correctly"
    )
    args = parser.parse_args()
    result = report(run(build_world(args.seed), args.system, args.split))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"matching-{args.system}-{args.split}.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(f"== {args.system}, {args.split} split")
    for title, section in (
        ("overall", "overall"),
        ("order reference", "by_order_reference"),
        ("payment", "by_payment"),
        ("payment reference", "by_payment_reference"),
    ):
        print(_table(title, result[section]))
    print(f"misses: {result['wrong_claims']} wrong claims, {result['left_open']} left open")
    if args.gate is not None and result["overall"]["all invoices"]["all"] < args.gate:
        print(f"GATE FAILED: share of fully correct matches below {args.gate}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
