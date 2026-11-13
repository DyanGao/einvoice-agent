"""API server for the browser tests of the review UI (ui/e2e).

Everything is in memory and in one process: SQLite, a queue that processes at
once, the rule-based extractor, a validator stub and the mock ERP. No Docker services,
no model calls. It also writes the sample invoice the browser test uploads.

    uv run python -m tests.ui_server --port 8010 --sample ui/e2e/.tmp/rechnung.pdf
"""

import argparse
import tempfile
from dataclasses import replace
from pathlib import Path

import uvicorn
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db
from app.api.main import create_app
from app.checks.service import Checker
from app.extract import BaselineExtractor
from app.settings import Settings
from eval.generators.canonical import XRECHNUNG
from eval.generators.world import PAID_ONCE, PO_EXACT, build_world
from eval.generators.xrechnung import render_ubl
from eval.match import erp_for
from tests.support import PDF, inline_queue

# The rule-based extractor reports no confidence. The browser test needs one
# uncertain field to check that the UI marks it.
UNCERTAIN_FIELD = "due_date"
# ... and one field that a rule filled in, to check that the UI says so.
DERIVED_FIELD = "currency"


class UncertainExtractor:
    def __init__(self) -> None:
        self._inner = BaselineExtractor()

    def extract(self, data: bytes):
        draft = self._inner.extract(data)
        return draft.model_copy(
            update={
                "field_confidence": {UNCERTAIN_FIELD: 0.35},
                "derived_fields": (DERIVED_FIELD,),
            }
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--ui-origin", default="http://localhost:3010")
    args = parser.parse_args()

    # The e-invoice comes from the matching world and the ERP below holds that
    # world, so it is clean and fully matched. The PDF's supplier is unknown to it.
    world = build_world()
    entry = next(
        entry
        for entry in world.invoices
        if entry.po_ref_kind == PO_EXACT and entry.payment_kind == PAID_ONCE
    )
    checker = Checker(erp_for(world))

    args.sample.parent.mkdir(parents=True, exist_ok=True)
    args.sample.write_bytes(PDF)
    args.sample.with_suffix(".xml").write_bytes(render_ubl(entry.invoice, XRECHNUNG))

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    db.Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    settings = replace(
        Settings.from_env(),
        feedback_dir=Path(tempfile.mkdtemp(prefix="einvoice-ui-test-")),
        ui_origin=args.ui_origin,
    )
    queue = inline_queue(session_factory, extractor=UncertainExtractor(), checker=checker)
    uvicorn.run(
        create_app(session_factory, queue, settings, checker), host="127.0.0.1", port=args.port
    )


if __name__ == "__main__":
    main()
