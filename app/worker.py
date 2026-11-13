"""The background worker:  python -m app.worker  (or `make worker`)."""

from functools import cache

import httpx
from redis import Redis
from rq import Queue, SimpleWorker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.extract.factory import build_extractor
from app.jobs import run_document_job
from app.queue import QUEUE_NAME
from app.settings import Settings
from app.validate import KositValidator


@cache
def _context() -> tuple:
    settings = Settings.from_env()
    client = httpx.Client(
        base_url=settings.validator_url, timeout=settings.validator_timeout_seconds
    )
    return (
        sessionmaker(create_engine(settings.database_url)),
        KositValidator(client),
        build_extractor(settings),
    )


def process_document_job(document_id: str) -> str:
    session_factory, validator, extractor = _context()
    return run_document_job(session_factory, document_id, validator, extractor)


def main() -> None:
    """Run a worker until interrupted. SimpleWorker executes jobs in this
    process instead of forking, which also works on macOS."""
    connection = Redis.from_url(Settings.from_env().redis_url)
    SimpleWorker([Queue(QUEUE_NAME, connection=connection)], connection=connection).work()


if __name__ == "__main__":
    main()
