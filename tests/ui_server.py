"""API server for the browser tests of the review UI (ui/e2e).

Everything is in memory and in one process: SQLite, a queue that processes at
once, the rule-based extractor, a validator stub and the mock ERP. No Docker services,
no model calls. It also writes the sample invoice the browser test uploads.

    uv run python -m tests.ui_server --port 8010 --sample ui/e2e/.tmp/rechnung.pdf
"""

import argparse
import asyncio
import tempfile
import threading
import time
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
from app.queue import JobQueue
from app.settings import Settings
from eval.generators.canonical import XRECHNUNG
from eval.generators.world import PAID_ONCE, PO_EXACT, build_world
from eval.generators.xrechnung import render_cii, render_ubl
from eval.match import erp_for
from tests.support import PDF, inline_queue

# The rule-based extractor reports no confidence. The browser test needs one
# uncertain field to check that the UI marks it.
UNCERTAIN_FIELD = "due_date"
# ... and one field that a rule filled in, to check that the UI says so.
DERIVED_FIELD = "currency"


# A document uploaded under a name with this prefix stays "received" for a while,
# like one the real worker has not got to yet, so the browser test can watch the
# UI update by itself. It is processed by the first request after the hold.
HOLD_PREFIX = "waiting-"
HOLD_SECONDS = 5.0


class HoldingQueue:
    def __init__(self, inner: JobQueue, session_factory: sessionmaker) -> None:
        self._inner = inner
        self._session_factory = session_factory
        self._held: dict[str, float] = {}
        self._lock = threading.Lock()

    def enqueue(self, document_id: str) -> None:
        with self._session_factory() as session:
            name = session.get(db.DocumentRow, document_id).filename or ""
        if name.startswith(HOLD_PREFIX):
            with self._lock:
                self._held[document_id] = time.monotonic() + HOLD_SECONDS
        else:
            self._inner.enqueue(document_id)

    def release_due(self) -> None:
        with self._lock:
            now = time.monotonic()
            due = [key for key, until in self._held.items() if until <= now]
            for document_id in due:
                del self._held[document_id]
                self._inner.enqueue(document_id)


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
    # The same e-invoice sent again as CII: a plain copy for "reject all duplicates".
    (args.sample.parent / f"{args.sample.stem}-copy.xml").write_bytes(
        render_cii(entry.invoice, XRECHNUNG)
    )
    # Other bytes than the sample, so it is a new document and not a duplicate.
    (args.sample.parent / f"{HOLD_PREFIX}{args.sample.name}").write_bytes(PDF + b"%held\n")
    # The same invoice sent again in another file: a duplicate, to be rejected.
    (args.sample.parent / f"again-{args.sample.name}").write_bytes(PDF + b"%again\n")

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    db.Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine)
    settings = replace(
        Settings.from_env(),
        feedback_dir=Path(tempfile.mkdtemp(prefix="einvoice-ui-test-")),
        ui_origin=args.ui_origin,
        # The review UI has no sign-in page yet (v1.0 Stage 1, increment 3).
        auth="off",
    )
    queue = HoldingQueue(
        inline_queue(session_factory, extractor=UncertainExtractor(), checker=checker),
        session_factory,
    )
    app = create_app(session_factory, queue, settings, checker)

    # One SQLite connection (StaticPool) serves every request, and it cannot be
    # used from two threads at once: the UI loads two lists in parallel, so
    # requests are handled one at a time here. The application itself runs on
    # Postgres with a connection pool.
    one_at_a_time = asyncio.Lock()

    @app.middleware("http")
    async def release_held(request, call_next):
        async with one_at_a_time:
            queue.release_due()
            return await call_next(request)

    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
