"""API server for the browser tests of the review UI (ui/e2e).

Everything is in memory and in one process: SQLite, a queue that processes at
once, the rule-based extractor, a validator stub and the mock ERP. No Docker services,
no model calls. It also writes the sample invoice the browser test uploads.

    uv run python -m tests.ui_server --port 8010 --sample ui/e2e/.tmp/rechnung.pdf

With `--auth on` sign-in is required (ui/e2e/sign-in.spec.ts): test accounts
with made-up passwords, and a stand-in identity provider under /fake-idp that
signs the browser in as ANNA without asking — or, when OIDC_ISSUER and the
other OIDC_* variables are set, the real provider they name.
"""

import argparse
import asyncio
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlencode

import uvicorn
from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db
from app.api.main import create_app
from app.auth import Role, add_membership, create_user
from app.auth.oidc import OidcClient
from app.checks.service import Checker
from app.extract import BaselineExtractor
from app.queue import JobQueue
from app.settings import OidcConfig, Settings
from app.tenants import create_tenant
from eval.generators.canonical import XRECHNUNG
from eval.generators.world import PAID_ONCE, PO_EXACT, build_world
from eval.generators.xrechnung import render_cii, render_ubl
from eval.match import erp_for
from tests.auth.oidc_provider import FakeProvider
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


# Test accounts for --auth on. Made up for the browser tests; ui/e2e/sign-in.spec.ts
# uses the same values.
ADMIN = ("admin@example.test", "Ada Admin", Role.ADMIN, "ui-test-admin-password")
VIEWER = ("viewer@example.test", "Vera Viewer", Role.VIEWER, "ui-test-viewer-password")
# Signs in through the stand-in identity provider only; FakeProvider's default email.
ANNA = ("anna@example.com", "Anna Example", Role.REVIEWER, None)


# A second company, with ADMIN in it too, for the company switcher.
ACME = ("acme", "Acme GmbH")


def add_test_accounts(session_factory: sessionmaker) -> None:
    with session_factory.begin() as session:
        users = {
            email: create_user(session, email, name, role, password)
            for email, name, role, password in (ADMIN, VIEWER, ANNA)
        }
        acme = create_tenant(session, *ACME)
        add_membership(session, users[ADMIN[0]].id, acme.id, Role.ADMIN)


def fake_identity_provider(port: int) -> FakeProvider:
    """Discovery and the token endpoint answer in process (FakeProvider); the
    authorization step is a real page the browser is sent to (`add_authorize`)."""
    base = f"http://localhost:{port}"
    config = OidcConfig(
        issuer=f"{base}/fake-idp",
        client_id="einvoice-ui-test",
        client_secret="ui-test-client-secret",
        redirect_uri=f"{base}/auth/oidc/callback",
        name="Test Login",
    )
    return FakeProvider(config)


def add_authorize(app, provider: FakeProvider) -> None:
    """The provider's sign-in page: signs the browser in as ANNA at once and
    sends it back with a code."""

    @app.get("/fake-idp/authorize", include_in_schema=False)
    def authorize(request: Request) -> RedirectResponse:
        code = provider.authorize(str(request.url))
        query = urlencode({"code": code, "state": request.query_params["state"]})
        return RedirectResponse(f"{provider.config.redirect_uri}?{query}", 303)


def add_mail_records(session_factory: sessionmaker) -> None:
    """Two mails the mailbox poller would have read, for the Mail page: one
    with nothing attached, one with a PDF it refused (made up)."""
    common = {"tenant_id": db.DEFAULT_TENANT_ID, "mailbox": "rechnungen@example.test/INBOX"}
    with session_factory.begin() as session:
        session.add_all(
            [
                db.MailRow(
                    **common,
                    uidvalidity=1,
                    uid=1,
                    message_id="<frage@example.test>",
                    sender="kunde@example.test",
                    subject="Frage zur Rechnung",
                    sent="Fri, 20 Nov 2026 09:00:00 +0100",
                    parts=[],
                ),
                db.MailRow(
                    **common,
                    uidvalidity=1,
                    uid=2,
                    message_id="<gesperrt@example.test>",
                    sender="lieferant@example.test",
                    subject="Ihre Rechnung",
                    sent="Fri, 20 Nov 2026 09:05:00 +0100",
                    parts=[
                        {
                            "filename": "gesperrt.pdf",
                            "content_type": "application/pdf",
                            "bytes": 2048,
                            "outcome": "refused",
                            "reason": "encrypted_pdf",
                            "document_id": None,
                        }
                    ],
                ),
            ]
        )


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
    parser.add_argument("--auth", choices=("on", "off"), default="off")
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
    add_mail_records(session_factory)
    settings = replace(
        Settings.from_env(),
        feedback_dir=Path(tempfile.mkdtemp(prefix="einvoice-ui-test-")),
        ui_origin=args.ui_origin,
        auth=args.auth,
    )
    queue = HoldingQueue(
        inline_queue(session_factory, extractor=UncertainExtractor(), checker=checker),
        session_factory,
    )
    if args.auth == "on" and settings.oidc is not None:
        # A real provider from OIDC_ISSUER etc. (e.g. Keycloak in Docker), to
        # try sign-in against the real thing by hand; no stand-in.
        add_test_accounts(session_factory)
        app = create_app(session_factory, queue, settings, checker)
    elif args.auth == "on":
        add_test_accounts(session_factory)
        provider = fake_identity_provider(args.port)
        oidc = OidcClient(provider.config, provider.http())
        app = create_app(session_factory, queue, settings, checker, oidc=oidc)
        add_authorize(app, provider)
    else:
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
