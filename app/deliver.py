"""The delivery loop:  python -m app.deliver  (or `make deliver`).

Every DELIVERY_SECONDS it books what has been approved or decided `auto`
and is not booked yet (app/delivery.py). Exactly once holds across restarts
and crashes; run one loop per database all the same, so that two do not
send the same booking at the same moment.
"""

import logging
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.connectors import ErpApiConnector
from app.delivery import deliver_due
from app.settings import Settings

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings.from_env()
    if settings.erp_url is None:
        raise SystemExit("ERP_URL is not set: there is no ERP to book invoices in")
    connectors = [
        ErpApiConnector(
            httpx.Client(base_url=settings.erp_url, timeout=settings.erp_timeout_seconds)
        )
    ]
    session_factory = sessionmaker(create_engine(settings.database_url))
    logger.info("delivering to %s every %ss", settings.erp_url, settings.delivery_seconds)
    while True:
        for document_id, connector, outcome in deliver_due(
            session_factory, connectors, datetime.now(UTC)
        ):
            logger.info("document %s to %s: %s", document_id, connector, outcome)
        time.sleep(settings.delivery_seconds)


if __name__ == "__main__":
    main()
