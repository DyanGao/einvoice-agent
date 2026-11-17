"""einvoice command line.

einvoice process FILE...     detect, validate, parse and store structured invoices now
einvoice watch DIR           queue every new file that appears in a folder
einvoice requeue             queue documents that were stored but never processed
"""

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app import db
from app.db.engine import database_engine
from app.ingest import requeue_received, scan_folder
from app.pipeline import DocumentStatus, Outcome, Validator, process_structured
from app.queue import JobQueue, RqQueue
from app.settings import Settings
from app.validate import KositValidator


def main(
    argv: Sequence[str] | None = None,
    *,
    validator: Validator | None = None,
    session_factory: sessionmaker[Session] | None = None,
    queue: JobQueue | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="einvoice")
    commands = parser.add_subparsers(dest="command", required=True)
    process = commands.add_parser("process", help="process structured e-invoices")
    process.add_argument("paths", nargs="+", type=Path)
    process.add_argument("--no-store", action="store_true", help="do not write to the database")
    watch = commands.add_parser("watch", help="queue new files from a folder")
    watch.add_argument("folder", type=Path)
    watch.add_argument("--interval", type=float, default=5.0, help="seconds between scans")
    watch.add_argument("--once", action="store_true", help="scan once and exit")
    commands.add_parser("requeue", help="queue stored documents that were never processed")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.command in ("watch", "requeue"):
        session_factory = session_factory or sessionmaker(database_engine(settings.database_url))
        queue = queue or RqQueue(settings.redis_url)
        if args.command == "requeue":
            print(f"queued {requeue_received(session_factory, queue)} documents")
            return 0
        return _watch(args, session_factory, queue, settings)

    if validator is None:
        validator = KositValidator(
            httpx.Client(
                base_url=settings.validator_url, timeout=settings.validator_timeout_seconds
            )
        )
    if session_factory is None and not args.no_store:
        session_factory = sessionmaker(database_engine(settings.database_url))

    all_valid = True
    for path in args.paths:
        report = _process_file(path, validator, None if args.no_store else session_factory)
        all_valid = all_valid and report["status"] == DocumentStatus.VALID.value
        print(json.dumps(report, ensure_ascii=False))
    return 0 if all_valid else 1


def _watch(
    args: argparse.Namespace,
    session_factory: sessionmaker[Session],
    queue: JobQueue,
    settings: Settings,
) -> int:
    if not args.folder.is_dir():
        print(f"not a folder: {args.folder}", file=sys.stderr)
        return 2
    while True:
        for item in scan_folder(args.folder, session_factory, queue, settings.max_upload_bytes):
            print(json.dumps({"document_id": item.document_id, "status": item.status}))
        if args.once:
            return 0
        time.sleep(args.interval)


def _process_file(
    path: Path, validator: Validator, session_factory: sessionmaker[Session] | None
) -> dict:
    data = path.read_bytes()
    if session_factory is None:
        return _report(path, process_structured(data, validator), document_id=None)

    with session_factory.begin() as session:
        existing = db.find_document(session, data)
        if existing is not None:
            db.record_duplicate(session, existing, path.name)
            return {
                "file": str(path),
                "document_id": existing.id,
                "status": existing.status,
                "duplicate": True,
            }
        outcome = process_structured(data, validator)
        document = db.store_outcome(session, data, path.name, outcome)
        return _report(path, outcome, document_id=document.id)


def _report(path: Path, outcome: Outcome, document_id: str | None) -> dict:
    validation = outcome.validation
    return {
        "file": str(path),
        "document_id": document_id,
        "format": outcome.format.value,
        "status": outcome.status.value,
        "error": outcome.error,
        "validation": None if validation is None else validation.model_dump(mode="json"),
        "invoice": None if outcome.invoice is None else outcome.invoice.model_dump(mode="json"),
    }


if __name__ == "__main__":
    sys.exit(main())
