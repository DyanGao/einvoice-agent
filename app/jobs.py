"""Processing of one stored document, as run by the worker."""

import logging

from sqlalchemy.orm import Session, sessionmaker

from app import db
from app.checks.service import Checker
from app.extract.base import Extractor
from app.pipeline import DocumentStatus, Validator, process_document

logger = logging.getLogger(__name__)


def run_document_job(
    session_factory: sessionmaker[Session],
    document_id: str,
    validator: Validator,
    extractor: Extractor | None,
    checker: Checker | None = None,
) -> str:
    """Process a received document and store the outcome. Returns its status.

    Safe to run twice: a document that is no longer `received` is left alone.
    The slow part (validator, model) runs outside any database transaction.
    With a checker, the business-rule checks run on the result right away.
    """
    with session_factory() as session:
        document = session.get(db.DocumentRow, document_id)
        if document is None:
            raise LookupError(f"no document {document_id}")
        if document.status != DocumentStatus.RECEIVED.value:
            return document.status
        data = document.content

    outcome = process_document(data, validator, extractor)

    with session_factory.begin() as session:
        document = session.get(db.DocumentRow, document_id, with_for_update=True)
        if document.status != DocumentStatus.RECEIVED.value:
            # Another worker finished it while this one was processing.
            return document.status
        db.apply_outcome(session, document, outcome)
        if checker is not None:
            checker.check(session, document)
        logger.info("document %s: %s", document_id, outcome.status.value)
        return outcome.status.value
