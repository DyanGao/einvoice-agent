"""HTTP API for upload and review.

With AUTH=on (the default) every route but /health and the sign-in routes
needs a signed-in user (a session cookie, from the identity provider or the
built-in password account) or an API token. With AUTH=off the API answers only
requests addressed to localhost, and the X-Reviewer header is a name for the
audit trail, not a proof of identity.
"""

import json
import logging
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import auth, db
from app.auth import Actor, Role, SignInRefused
from app.auth.limits import SignInLimiter
from app.auth.oidc import LoginAttempt, OidcClient, OidcError
from app.checks import Severity
from app.checks.service import Checker
from app.db.duplicates import redundant_copies, reject_copies
from app.db.rls import act_for
from app.extract.draft import InvoiceDraft
from app.ingest import ingest
from app.models import DocumentFormat
from app.queue import JobQueue, QueueUnavailable
from app.settings import Settings
from app.tenants import TENANT_ADMIN_KEYS, config_for, update_config
from app.texts import Text

if TYPE_CHECKING:
    from app.tenants import TenantServices

logger = logging.getLogger(__name__)

_INLINE_MEDIA_TYPES = {
    DocumentFormat.PDF_TEXT.value: "application/pdf",
    DocumentFormat.ZUGFERD_PDF.value: "application/pdf",
}
_IMAGE_SIGNATURES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))


class CopiesToReject(BaseModel):
    ids: list[str]


class Credentials(BaseModel):
    email: str
    password: str


SESSION_COOKIE = "eia_session"
# State, nonce and PKCE verifier between the start of a provider sign-in and
# its callback. SameSite=Lax: the callback is a navigation from the provider's
# site, on which a Strict cookie would not be sent.
LOGIN_COOKIE = "eia_oidc"
LOGIN_COOKIE_PATH = "/auth/oidc"
LOGIN_SECONDS = 600
# Sent by the review UI with every change. Another site can make a browser send
# the session cookie, but not a custom header (CORS allows it only to the UI).
UI_HEADER = "X-Requested-By"
UI_HEADER_VALUE = "einvoice-ui"
_READS = frozenset({"GET", "HEAD", "OPTIONS"})
# Swagger UI and ReDoc, which a strict Content-Security-Policy would break.
_PAGES_WITH_SCRIPTS = ("/docs", "/redoc")


class TenantChoice(BaseModel):
    tenant_id: str


class NewMember(BaseModel):
    email: str
    name: str
    role: Role


class RoleChange(BaseModel):
    role: Role


class SettingsChanges(BaseModel):
    changes: dict[str, Any]


class Reopening(BaseModel):
    note: str


class Rejection(BaseModel):
    reason: str  # one of db.RejectReason
    note: str | None = None


class FieldChanges(BaseModel):
    changes: dict[str, Any]


def _media_type(document: db.DocumentRow) -> str | None:
    """A type the browser may display, or None if it must only be downloaded."""
    if document.content.startswith(b"%PDF-"):
        return "application/pdf"
    for signature, media_type in _IMAGE_SIGNATURES:
        if document.content.startswith(signature):
            return media_type
    return None


def _summary(document: db.DocumentRow) -> dict:
    fields = document.draft.data if document.draft else {}
    return {
        "id": document.id,
        "filename": document.filename,
        "format": document.format,
        "status": document.status,
        "review_state": document.review_state,
        "created_at": document.created_at.isoformat(),
        # How many findings of the checks need a person.
        "findings_to_review": sum(
            finding.severity == Severity.REVIEW.value for finding in document.findings
        ),
        "number": fields.get("number"),
        "seller_name": (fields.get("seller") or {}).get("name"),
        "gross_total": fields.get("gross_total"),
        "currency": fields.get("currency"),
        # Where it was booked, or is waiting to be: the delivery per system.
        "bookings": [
            {
                "connector": delivery.connector,
                "status": delivery.status,
                "external_ref": delivery.external_ref,
            }
            for delivery in sorted(document.deliveries, key=lambda d: d.connector)
        ],
    }


def reason(exc: Exception) -> str:
    """An exception's message, keeping its code when it has one (a Text):
    str(exc) would make it plain."""
    message = exc.args[0] if exc.args else ""
    return message if isinstance(message, Text) else str(exc)


def _tenant(tenant: db.TenantRow) -> dict:
    return {"id": tenant.id, "slug": tenant.slug, "name": tenant.name}


def _me(session: Session, actor: Actor) -> dict:
    """Who is signed in, the tenant they work in, and every tenant they could
    switch to. `session` must still be able to read the sign-in tables."""
    tenants = auth.tenants_of(session, actor.user_id) if actor.user_id else []
    current = session.get(db.TenantRow, actor.tenant_id)
    return {
        "auth": "on",
        "name": actor.name,
        "email": actor.email,
        "role": actor.role.value,
        "tenant": _tenant(current),
        "tenants": [{**_tenant(tenant), "role": role.value} for tenant, role in tenants],
    }


def _detail(document: db.DocumentRow) -> dict:
    draft = document.draft
    validation = document.validation
    return {
        **_summary(document),
        "error": document.error,
        # None: the browser must not display this content, only download it.
        "content_type": _media_type(document),
        "fields": None if draft is None else draft.data,
        "original": None if draft is None else draft.original,
        "missing": (
            []
            if draft is None
            else db.missing_for_approval(InvoiceDraft.model_validate(draft.data))
        ),
        "validation": None
        if validation is None
        else {
            "valid": validation.valid,
            "scenario": validation.scenario,
            "violations": validation.violations,
        },
        # What the invoice was matched to in the ERP; None if the ERP was not asked.
        "match": None
        if document.match is None
        else {
            "supplier_id": document.match.supplier_id,
            "po_number": document.match.po_number,
            "po_basis": document.match.po_basis,
            "delivery_id": document.match.delivery_id,
            "payments": document.match.payments,
            "payment_basis": document.match.payment_basis,
            "explanation": document.match.explanation,
            # The same lines as codes for the UI's language (None: English only).
            "explanation_texts": document.match.explanation_texts,
        },
        "deliveries": [
            {
                "connector": delivery.connector,
                "status": delivery.status,
                "attempts": delivery.attempts,
                "external_ref": delivery.external_ref,
                "last_error": delivery.last_error,
                "error_text": delivery.error_text,
                "delivered_at": None
                if delivery.delivered_at is None
                else delivery.delivered_at.isoformat(),
            }
            for delivery in document.deliveries
        ],
        "decision": None
        if document.decision is None
        else {
            "route": document.decision.route,
            "reasons": document.decision.reasons,
            "version": document.decision.version,
        },
        "findings": [
            {
                "type": finding.type,
                "severity": finding.severity,
                "message": finding.message,
                "text": finding.text,
                "fields": finding.fields,
                "detail": finding.detail,
            }
            for finding in document.findings
        ],
        "audit": [
            {
                "seq": event.seq,
                "event": event.event,
                "actor": event.actor,
                "detail": event.detail,
                "created_at": event.created_at.isoformat(),
            }
            for event in document.audit_events
        ],
    }


def _write_feedback_case(settings: Settings, document: db.DocumentRow) -> None:
    """Keep what the reviewer changed as a future regression case. The folder
    is outside git by default because it holds real invoice data."""
    case = {
        "document_sha256": document.sha256,
        "format": document.format,
        "original": document.draft.original,
        "approved": document.draft.data,
        "corrections": [
            event.detail for event in document.audit_events if event.event == "field_corrected"
        ],
    }
    try:
        settings.feedback_dir.mkdir(parents=True, exist_ok=True)
        path = settings.feedback_dir / f"{document.sha256}.json"
        path.write_text(json.dumps(case, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        # The approval itself is committed; losing the eval case must not undo it.
        logger.exception("could not write feedback case for document %s", document.id)


def create_app(
    session_factory: sessionmaker[Session],
    queue: JobQueue,
    settings: Settings,
    checker: Checker | None = None,
    oidc: OidcClient | None = None,
    limiter: SignInLimiter | None = None,
    tenants: "TenantServices | None" = None,
) -> FastAPI:
    """`checker` re-runs the business-rule checks after a correction, so the
    findings always describe the draft as it stands. None: no checks.
    `oidc` talks to the identity provider; None: the one in `settings`, if any.
    `limiter` counts failed password sign-ins; None: a new one.
    `tenants` gives each tenant its own checker (its ERP); without it,
    `checker` serves the default tenant only."""
    app = FastAPI(title="einvoice-agent")

    @app.exception_handler(HTTPException)
    async def refusal(request: Request, exc: HTTPException) -> JSONResponse:
        """`detail` stays the English sentence, for every client as before; a
        refusal written as a Text also says its code, for the review UI's
        language (app/texts.py)."""
        body: dict[str, Any] = {"detail": exc.detail}
        if isinstance(exc.detail, Text):
            body = {"detail": str(exc.detail), "text": exc.detail.said()}
        return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)

    if oidc is None and settings.oidc is not None:
        oidc = OidcClient(settings.oidc)
    limiter = limiter or SignInLimiter()

    def secure(request: Request) -> bool:
        return settings.cookie_secure == "true" or request.url.scheme == "https"

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        # Sign-in addresses carry codes; nothing here should travel on as a referrer.
        headers.setdefault("Referrer-Policy", "no-referrer")
        # Invoices and sessions: nothing for a shared cache or the back button.
        headers.setdefault("Cache-Control", "no-store")
        if request.url.path.startswith(_PAGES_WITH_SCRIPTS):
            pass  # the interactive API docs load their own scripts and styles
        elif request.url.path.endswith("/content") and request.url.path.startswith("/documents/"):
            # The original document is shown inside the review UI, and only there.
            headers.setdefault("Content-Security-Policy", f"frame-ancestors {settings.ui_origin}")
        else:
            headers.setdefault(
                "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
            )
            headers.setdefault("X-Frame-Options", "DENY")
        if secure(request):
            headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.ui_origin],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Reviewer", "Authorization", UI_HEADER],
        allow_credentials=True,
    )

    def session() -> Iterator[Session]:
        with session_factory.begin() as active:
            yield active

    def typed_name(x_reviewer: str | None) -> str:
        name = (x_reviewer or "").strip()
        if not name or len(name) > 100:
            raise HTTPException(400, Text("api.reviewer_name_required"))
        return name

    def identify(request: Request, active: Session) -> Actor | None:
        """The signed-in user or token owner; None when neither is there."""
        now = datetime.now(UTC)
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return auth.authenticate_token(active, header.removeprefix("Bearer ").strip(), now)
        cookie = request.cookies.get(SESSION_COOKIE)
        if cookie is None:
            return None
        if request.method not in _READS and request.headers.get(UI_HEADER) != UI_HEADER_VALUE:
            raise HTTPException(403, Text("api.csrf_header", header=UI_HEADER))
        return auth.authenticate_session(active, cookie, now)

    def require(role: Role, *, named: bool = False):
        """A dependency: the request's actor, who must have at least `role`.
        With sign-in off, anyone on this machine acts; `named` actions record the
        name typed into the UI, unverified, as before sign-in existed."""

        def dependency(
            request: Request,
            active: Annotated[Session, Depends(session)],
            x_reviewer: Annotated[str | None, Header()] = None,
        ) -> Actor:
            if settings.auth == "off":
                if (request.url.hostname or "") not in settings.open_hosts:
                    raise HTTPException(
                        403,
                        Text("api.open_mode_localhost"),
                    )
                name = typed_name(x_reviewer) if named else "anonymous"
                actor = Actor(
                    name=name, role=Role.ADMIN, tenant_id=db.DEFAULT_TENANT_ID, verified=False
                )
                act_for(active, actor.tenant_id)
                return actor
            # Once per request: after act_for the transaction may no longer
            # read the sign-in tables.
            actor = getattr(request.state, "actor", None)
            if actor is None:
                actor = identify(request, active)
                if actor is None:
                    raise HTTPException(
                        401,
                        Text("api.sign_in"),
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                request.state.actor = actor
                # Postgres hides every other tenant's rows from here on.
                act_for(active, actor.tenant_id)
            if not actor.role.allows(role):
                raise HTTPException(403, Text("api.needs_role", role=Text(f"role.{role}")))
            return actor

        return dependency

    def document(
        document_id: str,
        active: Annotated[Session, Depends(session)],
        actor: Annotated[Actor, Depends(require(Role.VIEWER))],
    ) -> db.DocumentRow:
        """Signed in first: a stranger must not learn which documents exist.
        Another tenant's document does not exist either (404, not 403)."""
        row = active.get(db.DocumentRow, document_id)
        if row is None or row.tenant_id != actor.tenant_id:
            raise HTTPException(404, Text("api.document_not_found"))
        return row

    def checker_for(row: db.DocumentRow) -> Checker | None:
        """The document's tenant's checks, against that tenant's ERP."""
        if tenants is not None:
            return tenants.checker(row.tenant_id)
        return checker if row.tenant_id == db.DEFAULT_TENANT_ID else None

    Db = Annotated[Session, Depends(session)]
    Doc = Annotated[db.DocumentRow, Depends(document)]
    Viewer = Annotated[Actor, Depends(require(Role.VIEWER))]
    Uploader = Annotated[Actor, Depends(require(Role.REVIEWER))]
    Reviewer = Annotated[Actor, Depends(require(Role.REVIEWER, named=True))]
    Admin = Annotated[Actor, Depends(require(Role.ADMIN))]

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        """Someone opened the bare address in a browser: show them the API page."""
        return RedirectResponse("/docs")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    def start_session(active: Session, actor: Actor, request: Request, response: Response):
        session_id = auth.start_session(active, actor.user_id, actor.tenant_id, datetime.now(UTC))
        response.set_cookie(
            SESSION_COOKIE,
            session_id,
            max_age=int(auth.SESSION_LIFETIME.total_seconds()),
            httponly=True,
            samesite="strict",
            secure=secure(request),
        )

    def back_to_ui(outcome: str | None = None) -> RedirectResponse:
        target = settings.ui_origin.rstrip("/") + "/"
        if outcome is not None:
            target += f"?signin={outcome}"
        response = RedirectResponse(target, status_code=303)
        response.delete_cookie(LOGIN_COOKIE, path=LOGIN_COOKIE_PATH)
        return response

    def provider() -> OidcClient:
        if settings.auth == "off":
            raise HTTPException(409, Text("api.sign_in_off"))
        if oidc is None:
            raise HTTPException(404, Text("api.no_provider"))
        return oidc

    @app.get("/auth/options")
    def sign_in_options() -> dict:
        """What the sign-in page offers. The password form is always there with
        sign-in on: it is how the built-in admin gets in."""
        if settings.auth == "off":
            return {"auth": "off"}
        provider_offered = oidc is not None
        return {
            "auth": "on",
            "provider": provider_offered,
            "provider_name": oidc.config.name if provider_offered else None,
        }

    @app.post("/auth/login")
    def login(body: Credentials, response: Response, request: Request, active: Db) -> dict:
        """Password sign-in, for the built-in account(s) set up with the CLI."""
        if settings.auth == "off":
            raise HTTPException(409, Text("api.sign_in_off"))
        address = request.client.host if request.client else "unknown"
        wait = limiter.retry_after(body.email, address)
        if wait is not None:
            raise HTTPException(
                429,
                Text("api.too_many_sign_ins", minutes=(wait + 59) // 60),
                headers={"Retry-After": str(wait)},
            )
        actor = auth.sign_in(active, body.email, body.password)
        if actor is None:
            limiter.failed(body.email, address)
            raise HTTPException(401, Text("api.wrong_password"))
        limiter.succeeded(body.email)
        start_session(active, actor, request, response)
        return _me(active, actor)

    @app.get("/auth/oidc/start")
    def oidc_start(request: Request) -> RedirectResponse:
        """Send the browser to the identity provider."""
        client = provider()
        attempt = LoginAttempt.new()
        try:
            target = client.authorization_url(attempt)
        except OidcError as exc:
            logger.warning("identity provider unavailable: %s", exc)
            raise HTTPException(502, Text("api.provider_unreachable")) from exc
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            LOGIN_COOKIE,
            attempt.cookie(),
            max_age=LOGIN_SECONDS,
            path=LOGIN_COOKIE_PATH,
            httponly=True,
            samesite="lax",
            secure=secure(request),
        )
        return response

    @app.get("/auth/oidc/callback")
    def oidc_callback(
        request: Request,
        active: Db,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> RedirectResponse:
        """The provider sends the browser back here; on to the UI, signed in
        or with `?signin=` saying why not."""
        client = provider()
        if error is not None:
            return back_to_ui("cancelled")
        attempt = LoginAttempt.from_cookie(request.cookies.get(LOGIN_COOKIE))
        if attempt is None or not secrets.compare_digest(state or "", attempt.state) or not code:
            # Expired, opened in another browser, or not started here at all.
            return back_to_ui("failed")
        try:
            identity = client.identify(code, attempt, datetime.now(UTC))
        except OidcError as exc:
            logger.warning("sign-in through the identity provider failed: %s", exc)
            return back_to_ui("failed")
        try:
            actor = auth.sign_in_with_provider(
                active,
                identity.issuer,
                identity.subject,
                identity.email,
                identity.email_verified,
                client.config.require_verified_email,
            )
        except SignInRefused as refused:
            logger.info("sign-in refused: %s", refused)
            return back_to_ui(refused.reason)
        response = back_to_ui()
        start_session(active, actor, request, response)
        return response

    @app.post("/auth/logout", status_code=204)
    def logout(request: Request, active: Db) -> Response:
        cookie = request.cookies.get(SESSION_COOKIE)
        if cookie is not None:
            auth.end_session(active, cookie)
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/auth/me")
    def me(request: Request, active: Db) -> dict:
        if settings.auth == "off":
            return {"auth": "off"}
        actor = identify(request, active)
        if actor is None:
            raise HTTPException(401, Text("api.not_signed_in"))
        return _me(active, actor)

    def browser_session(request: Request, active: Db) -> Actor:
        """Signed in with the session cookie: checked before anything else of
        the request is looked at."""
        if settings.auth == "off":
            raise HTTPException(409, Text("api.sign_in_off"))
        if request.headers.get("Authorization", "").startswith("Bearer "):
            raise HTTPException(409, Text("api.token_cannot_switch"))
        actor = identify(request, active)
        if actor is None:
            raise HTTPException(
                401, Text("api.not_signed_in"), headers={"WWW-Authenticate": "Bearer"}
            )
        return actor

    @app.post("/auth/tenant")
    def choose_tenant(
        body: TenantChoice,
        request: Request,
        active: Db,
        _: Annotated[Actor, Depends(browser_session)],
    ) -> dict:
        """Work in another tenant one is in, in this browser session. A token
        acts in the tenant it was made for and cannot switch."""
        actor = auth.switch_tenant(
            active, request.cookies[SESSION_COOKIE], body.tenant_id, datetime.now(UTC)
        )
        if actor is None:
            # Like a document of another tenant: not there, rather than forbidden.
            raise HTTPException(404, Text("api.no_such_tenant"))
        return _me(active, actor)

    @contextmanager
    def accounts() -> Iterator[Session]:
        """A transaction of its own for the members and settings pages: the
        request's own one acts for the tenant (row-level security) and may
        not read the sign-in tables. Everything here is filtered by the
        admin's tenant in the code."""
        if settings.auth == "off":
            raise HTTPException(409, Text("api.members_cli"))
        with session_factory.begin() as session:
            yield session

    def member(user: db.UserRow, role: Role) -> dict:
        return {
            "user_id": user.id,
            "email": user.email,
            "name": user.display_name,
            "role": role.value,
            "disabled": user.disabled,
            # The built-in password account(s); everyone else uses the provider.
            "password": user.password_hash is not None,
        }

    @app.get("/admin/members")
    def list_members(who: Admin) -> dict:
        with accounts() as session:
            return {"members": [member(u, r) for u, r in auth.members_of(session, who.tenant_id)]}

    @app.post("/admin/members", status_code=201)
    def add_member(body: NewMember, who: Admin) -> dict:
        """Someone new signs in through the identity provider; someone who is
        already a user (in another tenant) gets a role here too."""
        with accounts() as session:
            user = auth.find_user(session, body.email)
            try:
                if user is None:
                    user = auth.create_user(
                        session, body.email, body.name, body.role, tenant_id=who.tenant_id
                    )
                elif session.get(db.MembershipRow, (user.id, who.tenant_id)) is not None:
                    raise HTTPException(409, Text("api.member_already", email=user.email))
                else:
                    auth.add_membership(session, user.id, who.tenant_id, body.role)
            except ValueError as exc:
                raise HTTPException(422, Text("api.member_refused", detail=str(exc))) from exc
            return member(user, body.role)

    @app.patch("/admin/members/{user_id}")
    def change_member(user_id: str, body: RoleChange, who: Admin) -> dict:
        with accounts() as session:
            try:
                auth.change_role(session, who.tenant_id, user_id, body.role, who.user_id)
            except LookupError as exc:
                raise HTTPException(404, reason(exc)) from exc
            except auth.MembershipError as exc:
                raise HTTPException(409, reason(exc)) from exc
            return member(session.get(db.UserRow, user_id), body.role)

    @app.delete("/admin/members/{user_id}", status_code=204)
    def remove_member(user_id: str, who: Admin) -> Response:
        with accounts() as session:
            try:
                auth.remove_member(session, who.tenant_id, user_id, who.user_id)
            except LookupError as exc:
                raise HTTPException(404, reason(exc)) from exc
            except auth.MembershipError as exc:
                raise HTTPException(409, reason(exc)) from exc
        return Response(status_code=204)

    @app.get("/admin/settings")
    def tenant_settings(who: Admin) -> dict:
        """The tenant's settings. Those that reach the server's network and
        files are the operator's (einvoice tenant-set) and only shown."""
        with accounts() as session:
            config = config_for(session, who.tenant_id, settings)
            return {
                "tenant": _tenant(session.get(db.TenantRow, who.tenant_id)),
                "settings": config.model_dump(mode="json"),
                "editable": list(TENANT_ADMIN_KEYS),
            }

    @app.patch("/admin/settings")
    def change_tenant_settings(body: SettingsChanges, who: Admin) -> dict:
        refused = sorted(set(body.changes) - set(TENANT_ADMIN_KEYS))
        if refused:
            raise HTTPException(
                403,
                Text("api.operator_settings", settings=refused),
            )
        with accounts() as session:
            try:
                update_config(session, who.tenant_id, body.changes, settings)
            except ValueError as exc:
                raise HTTPException(422, Text("api.invalid_settings", detail=str(exc))) from exc
        return tenant_settings(who)

    @app.post("/documents", status_code=202)
    async def upload(file: UploadFile, response: Response, who: Uploader) -> dict:
        data = await file.read(settings.max_upload_bytes + 1)
        if not data:
            raise HTTPException(400, Text("api.empty_file"))
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, Text("api.file_too_large", bytes=settings.max_upload_bytes))
        try:
            result = ingest(session_factory, queue, data, file.filename, tenant_id=who.tenant_id)
        except QueueUnavailable as exc:
            raise HTTPException(503, Text("api.not_queued", detail=str(exc))) from exc
        if result.duplicate:
            response.status_code = 200
        return {"id": result.document_id, "status": result.status, "duplicate": result.duplicate}

    @app.get("/documents")
    def list_documents(
        active: Db,
        who: Viewer,
        review_state: db.ReviewState | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict:
        query = (
            select(db.DocumentRow)
            .where(db.DocumentRow.tenant_id == who.tenant_id)
            .order_by(db.DocumentRow.created_at.desc())
        )
        if review_state is not None:
            query = query.where(db.DocumentRow.review_state == review_state.value)
        rows = active.scalars(query.limit(limit).offset(offset)).all()
        return {"documents": [_summary(row) for row in rows]}

    @app.get("/documents/{document_id}")
    def get_document(row: Doc, _: Viewer) -> dict:
        return _detail(row)

    @app.get("/documents/{document_id}/content")
    def get_content(row: Doc, _: Viewer) -> Response:
        media_type = _media_type(row)
        headers = {"X-Content-Type-Options": "nosniff"}
        if media_type is None:
            # XML and unknown uploads are never rendered by the browser.
            media_type = "application/octet-stream"
            headers["Content-Disposition"] = 'attachment; filename="document.bin"'
        else:
            headers["Content-Disposition"] = "inline"
        return Response(row.content, media_type=media_type, headers=headers)

    @app.patch("/documents/{document_id}/fields")
    def patch_fields(row: Doc, body: FieldChanges, active: Db, who: Reviewer) -> dict:
        try:
            changed = db.correct_fields(active, row, body.changes, who.name, who.user_id)
        except db.NotReviewable as exc:
            raise HTTPException(409, reason(exc)) from exc
        except db.ReviewError as exc:
            raise HTTPException(422, reason(exc)) from exc
        checks = checker_for(row)
        if checks is not None and changed:
            checks.check(active, row)
        return {"changed": changed, **_detail(row)}

    @app.post("/documents/{document_id}/recheck")
    def recheck(row: Doc, active: Db, _: Uploader) -> dict:
        """Match and check again. What is in the ERP and on file changes after
        an invoice was processed: its payment arrives, the other invoices of a
        combined transfer come in, a supplier's master data is corrected."""
        checks = checker_for(row)
        if checks is None:
            raise HTTPException(409, Text("api.no_checks"))
        if row.review_state == db.ReviewState.APPROVED.value:
            raise HTTPException(409, Text("review.already_approved"))
        if row.review_state == db.ReviewState.REJECTED.value:
            raise HTTPException(409, Text("review.rejected"))
        if row.draft is None:
            raise HTTPException(409, Text("api.nothing_to_check"))
        checks.check(active, row)
        return _detail(row)

    @app.post("/documents/{document_id}/approve")
    def approve(row: Doc, active: Db, who: Reviewer) -> dict:
        try:
            db.approve(active, row, who.name, who.user_id)
        except db.NotReviewable as exc:
            raise HTTPException(409, reason(exc)) from exc
        except db.ReviewError as exc:
            raise HTTPException(422, reason(exc)) from exc
        _write_feedback_case(settings, row)
        return _detail(row)

    @app.get("/mail")
    def list_mail(
        active: Db,
        who: Viewer,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict:
        """The mails the mailbox poller read, newest first, with what became of
        each attached file: so nothing sent in disappears without trace."""
        rows = active.scalars(
            select(db.MailRow)
            .where(db.MailRow.tenant_id == who.tenant_id)
            .order_by(db.MailRow.created_at.desc(), db.MailRow.id)
            .limit(limit)
            .offset(offset)
        )
        return {
            "mails": [
                {
                    "id": row.id,
                    "read_at": row.created_at.isoformat(),
                    "sender": row.sender,
                    "subject": row.subject,
                    "sent": row.sent,
                    "parts": row.parts,
                }
                for row in rows
            ]
        }

    @app.get("/review/duplicates")
    def duplicate_copies(active: Db, who: Viewer) -> dict:
        """Plain copies of an earlier invoice, waiting for review only for being
        copies (app/db/duplicates.py): what "reject all duplicates" would reject."""

        return {
            "copies": [
                {**_summary(copy.document), "original": _summary(copy.original)}
                for copy in redundant_copies(active, who.tenant_id)
            ]
        }

    @app.post("/review/duplicates/reject")
    def reject_duplicate_copies(body: CopiesToReject, active: Db, who: Reviewer) -> dict:
        """Reject the listed documents that are still plain copies; the rest are
        skipped, so a list shown earlier cannot reject what has changed since."""
        result = reject_copies(active, who.tenant_id, body.ids, who.name, who.user_id)
        return {"rejected": result.rejected, "skipped": result.skipped}

    @app.post("/documents/{document_id}/reject")
    def reject(row: Doc, body: Rejection, active: Db, who: Reviewer) -> dict:
        """Not to be booked, with the reason: a duplicate, an invoice meant for
        someone else, one replaced by a correction. Final, and never delivered."""
        try:
            db.reject(active, row, who.name, body.reason, body.note, who.user_id)
        except db.NotReviewable as exc:
            raise HTTPException(409, reason(exc)) from exc
        except db.ReviewError as exc:
            raise HTTPException(422, reason(exc)) from exc
        return _detail(row)

    @app.post("/documents/{document_id}/reopen")
    def reopen(row: Doc, body: Reopening, active: Db, who: Reviewer) -> dict:
        """A rejection was a mistake: back to review, with a note saying why."""
        try:
            db.reopen(active, row, who.name, body.note, who.user_id)
        except db.NotReviewable as exc:
            raise HTTPException(409, reason(exc)) from exc
        except db.ReviewError as exc:
            raise HTTPException(422, reason(exc)) from exc
        return _detail(row)

    return app


def create_app_from_env() -> FastAPI:
    """For `uvicorn --factory app.api.main:create_app_from_env`."""
    from app.db.engine import database_engine
    from app.queue import RqQueue
    from app.tenants import TenantServices

    settings = Settings.from_env()
    session_factory = sessionmaker(database_engine(settings.database_url))
    return create_app(
        session_factory,
        RqQueue(settings.redis_url),
        settings,
        tenants=TenantServices(session_factory, settings),
    )
