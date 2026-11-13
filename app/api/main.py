"""HTTP API for upload and review.

There is no authentication yet. The server is meant to listen on localhost
only, and the X-Reviewer header is a name for the audit trail, not a proof of
identity.
"""

import json
import logging
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import db
from app.extract.draft import InvoiceDraft
from app.ingest import ingest
from app.models import DocumentFormat
from app.queue import JobQueue, QueueUnavailable
from app.settings import Settings

logger = logging.getLogger(__name__)

_INLINE_MEDIA_TYPES = {
    DocumentFormat.PDF_TEXT.value: "application/pdf",
    DocumentFormat.ZUGFERD_PDF.value: "application/pdf",
}
_IMAGE_SIGNATURES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))


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
        "number": fields.get("number"),
        "seller_name": (fields.get("seller") or {}).get("name"),
        "gross_total": fields.get("gross_total"),
        "currency": fields.get("currency"),
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
    session_factory: sessionmaker[Session], queue: JobQueue, settings: Settings
) -> FastAPI:
    app = FastAPI(title="einvoice-agent")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.ui_origin],
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type", "X-Reviewer"],
    )

    def session() -> Iterator[Session]:
        with session_factory.begin() as active:
            yield active

    def reviewer(x_reviewer: Annotated[str | None, Header()] = None) -> str:
        name = (x_reviewer or "").strip()
        if not name or len(name) > 100:
            raise HTTPException(400, "X-Reviewer header with the reviewer's name is required")
        return name

    def document(document_id: str, active: Annotated[Session, Depends(session)]) -> db.DocumentRow:
        row = active.get(db.DocumentRow, document_id)
        if row is None:
            raise HTTPException(404, "document not found")
        return row

    Db = Annotated[Session, Depends(session)]
    Doc = Annotated[db.DocumentRow, Depends(document)]
    Reviewer = Annotated[str, Depends(reviewer)]

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/documents", status_code=202)
    async def upload(file: UploadFile, response: Response) -> dict:
        data = await file.read(settings.max_upload_bytes + 1)
        if not data:
            raise HTTPException(400, "empty file")
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, f"file larger than {settings.max_upload_bytes} bytes")
        try:
            result = ingest(session_factory, queue, data, file.filename)
        except QueueUnavailable as exc:
            raise HTTPException(
                503, f"document stored but not queued ({exc}); run `einvoice requeue`"
            ) from exc
        if result.duplicate:
            response.status_code = 200
        return {"id": result.document_id, "status": result.status, "duplicate": result.duplicate}

    @app.get("/documents")
    def list_documents(
        active: Db,
        review_state: db.ReviewState | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict:
        query = select(db.DocumentRow).order_by(db.DocumentRow.created_at.desc())
        if review_state is not None:
            query = query.where(db.DocumentRow.review_state == review_state.value)
        rows = active.scalars(query.limit(limit).offset(offset)).all()
        return {"documents": [_summary(row) for row in rows]}

    @app.get("/documents/{document_id}")
    def get_document(row: Doc) -> dict:
        return _detail(row)

    @app.get("/documents/{document_id}/content")
    def get_content(row: Doc) -> Response:
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
            changed = db.correct_fields(active, row, body.changes, who)
        except db.NotReviewable as exc:
            raise HTTPException(409, str(exc)) from exc
        except db.ReviewError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"changed": changed, **_detail(row)}

    @app.post("/documents/{document_id}/approve")
    def approve(row: Doc, active: Db, who: Reviewer) -> dict:
        try:
            db.approve(active, row, who)
        except db.NotReviewable as exc:
            raise HTTPException(409, str(exc)) from exc
        except db.ReviewError as exc:
            raise HTTPException(422, str(exc)) from exc
        _write_feedback_case(settings, row)
        return _detail(row)

    return app


def create_app_from_env() -> FastAPI:
    """For `uvicorn --factory app.api.main:create_app_from_env`."""
    from sqlalchemy import create_engine

    from app.queue import RqQueue

    settings = Settings.from_env()
    return create_app(
        sessionmaker(create_engine(settings.database_url)), RqQueue(settings.redis_url), settings
    )
