"""The README demo, end to end:  make demo

Five invoices from sample_invoices/ go through the application as it runs:
parsing and validation, matching and checks against the mock ERP, the
decision engine, and delivery to the ERP's booking API. Four are booked
without review; one is flagged for a price 12 % above its order; a reviewer
approves it; then it is booked too. Every step is printed.

It uses its own database (`einvoice_demo` next to yours in the compose
Postgres) and its own mock ERP (`data/demo/erp.sqlite3`), both made fresh on
every run, so your own data is not touched. It needs the validator and
Postgres running (`docker compose up -d` or `make up`) and no API key: all
five are e-invoices, read without a model. It exits non-zero if the story
did not happen as told, so CI can run it.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app import db
from app.checks.service import Checker
from app.connectors import ErpApiConnector
from app.db.review import ReviewState, approve
from app.delivery import deliver_due
from app.erp import HttpErpClient
from app.ingest import ingest
from app.jobs import run_document_job
from app.pipeline import Validator
from app.queue import InlineQueue

REVIEWER = "demo-reviewer"
SAMPLE_DIR = Path("sample_invoices")


@dataclass
class DemoResult:
    processed: list[str] = field(default_factory=list)
    booked_without_review: list[str] = field(default_factory=list)
    flagged: str | None = None
    flagged_findings: list[str] = field(default_factory=list)
    booked_before_approval: list[str] = field(default_factory=list)
    bookings: list[dict] = field(default_factory=list)


def problems(result: DemoResult) -> list[str]:
    """Where the story differs from the README's."""
    found = []
    if len(result.processed) != 5:
        found.append(f"{len(result.processed)} invoices processed, not 5")
    if len(result.booked_without_review) != 4:
        found.append(f"{len(result.booked_without_review)} booked without review, not 4")
    if result.flagged is None or result.flagged_findings != ["price_deviation"]:
        found.append(f"flagged for {result.flagged_findings or 'nothing'}, not a price deviation")
    if len(result.bookings) != 5:
        found.append(f"{len(result.bookings)} bookings in the ERP at the end, not 5")
    return found


def run_demo(
    session_factory: sessionmaker[Session],
    erp: httpx.Client,
    validator: Validator,
    samples: Path = SAMPLE_DIR,
    say: Callable[[str], None] = print,
) -> DemoResult:
    result = DemoResult()
    checker = Checker(HttpErpClient(erp))
    queue = InlineQueue(
        lambda document_id: run_document_job(session_factory, document_id, validator, None, checker)
    )
    connector = ErpApiConnector(erp)
    files = sorted(path for path in samples.iterdir() if path.suffix in (".xml", ".pdf"))

    say(f"1. {len(files)} invoices arrive")
    names: dict[str, str] = {}
    for path in files:
        document_id = ingest(session_factory, queue, path.read_bytes(), path.name).document_id
        names[document_id] = path.name
        result.processed.append(document_id)
        with session_factory() as session:
            document = session.get(db.DocumentRow, document_id)
            match = document.match
            route = document.decision.route if document.decision else "none"
            say(
                f"   {path.name:<26} {document.format:<12} {document.status:<9} "
                f"order {match.po_number if match else '-':<10} → {route}"
            )
            if document.review_state == ReviewState.NOT_REQUIRED.value:
                result.booked_without_review.append(document_id)
            else:
                result.flagged = document_id
                result.flagged_findings = [
                    finding.type for finding in document.findings if finding.severity == "review"
                ]
                for finding in document.findings:
                    say(f"      flagged: {finding.message}")

    say("2. delivery: what needs no review is booked in the ERP")
    for document_id, _, outcome in deliver_due(session_factory, [connector], datetime.now(UTC)):
        say(f"   {names[document_id]:<26} {outcome}")
        if outcome == "delivered":
            result.booked_before_approval.append(document_id)

    if result.flagged is not None:
        say(f"3. {REVIEWER} checks {names[result.flagged]} and approves it")
        with session_factory.begin() as session:
            approve(session, session.get(db.DocumentRow, result.flagged), REVIEWER)
        for document_id, _, outcome in deliver_due(session_factory, [connector], datetime.now(UTC)):
            say(f"   {names[document_id]:<26} {outcome}")

    result.bookings = erp.get("/booked-invoices").json()["booked_invoices"]
    say(f"4. the ERP now holds {len(result.bookings)} bookings")
    for booking in result.bookings:
        say(
            f"   no. {booking['id']:<3} {booking['number']:<16} {booking['supplier_id']} "
            f"{booking['gross_total']:>10} {booking['currency']}  order {booking['po_number']}"
        )
    return result


# --- running it for real ----------------------------------------------------


def _fresh_database(url: str) -> str:
    """A fresh demo database next to the configured one; its URL."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    configured = make_url(url)
    if not configured.drivername.startswith("postgresql"):
        path = Path("data/demo/einvoice.sqlite3")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        return f"sqlite:///{path}"
    admin = create_engine(configured, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text("DROP DATABASE IF EXISTS einvoice_demo WITH (FORCE)"))
        connection.execute(text("CREATE DATABASE einvoice_demo"))
    admin.dispose()
    return configured.set(database="einvoice_demo").render_as_string(hide_password=False)


def _migrate(url: str) -> None:
    import os

    from alembic import command
    from alembic.config import Config

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url  # migrations/env.py reads it
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def main() -> int:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    from app.settings import Settings
    from app.validate import KositValidator
    from eval.generators.world import build_world
    from mock_erp import seed
    from mock_erp.main import create_app as create_erp

    settings = Settings.from_env()
    try:
        # Any answer will do: the validator has no health endpoint (compose
        # only checks that its port is open).
        httpx.get(settings.validator_url, timeout=3)
    except httpx.HTTPError:
        print(f"the validator at {settings.validator_url} is not running: start it with `make up`")
        return 2
    try:
        url = _fresh_database(settings.database_url)
    except Exception as exc:  # noqa: BLE001 - one message for every way Postgres is not there
        print(f"the database at {settings.database_url} is not reachable ({exc}): `make up`")
        return 2
    _migrate(url)
    erp_file = Path("data/demo/erp.sqlite3")
    erp_file.parent.mkdir(parents=True, exist_ok=True)
    erp_file.unlink(missing_ok=True)
    erp_engine = create_engine(f"sqlite:///{erp_file}")
    seed.load(build_world(), erp_engine)

    print("einvoice-agent demo — five synthetic invoices, no model, no API key\n")
    result = run_demo(
        sessionmaker(create_engine(url)),
        TestClient(create_erp(erp_engine)),
        KositValidator(
            httpx.Client(
                base_url=settings.validator_url, timeout=settings.validator_timeout_seconds
            )
        ),
    )
    found = problems(result)
    print()
    if found:
        print("DEMO DID NOT GO AS TOLD: " + "; ".join(found))
        return 1
    print("As in the README: 5 in, 4 booked without review, 1 flagged for its price, approved,")
    print("booked. To look around:")
    print(f"  the ERP's screens:  MOCK_ERP_DATABASE_URL=sqlite:///{erp_file} make erp")
    print("                     then http://127.0.0.1:8100/ui/bookings")
    shown = make_url(url).render_as_string(hide_password=True)
    print(f"  the review UI:      DATABASE_URL={shown} make api, then make ui")
    print("                     (with your password in place of ***)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
