"""The delivery loop:  python -m app.deliver  (or `make deliver`).

Every DELIVERY_SECONDS it books what has been approved or decided `auto`
and is not booked yet (app/delivery.py), in every system configured: the
ERP's API (ERP_URL) and the DATEV-style booking lists (EXPORT_DIR).
Exactly once holds across restarts and crashes; run one loop per database
all the same, so that two do not send the same booking at the same moment.
"""

import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.connectors import Connector, CsvExportConnector, ErpApiConnector
from app.delivery import deliver_due
from app.settings import Settings

logger = logging.getLogger(__name__)


def build_connectors(settings: Settings) -> list[Connector]:
    """Every downstream system this installation is configured for."""
    connectors: list[Connector] = []
    if settings.erp_url is not None:
        client = httpx.Client(base_url=settings.erp_url, timeout=settings.erp_timeout_seconds)
        connectors.append(ErpApiConnector(client))
    if settings.export_dir is not None:
        connectors.append(CsvExportConnector(Path(settings.export_dir)))
    return connectors


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings.from_env()
    connectors = build_connectors(settings)
    if not connectors:
        raise SystemExit("nothing to deliver to: set ERP_URL, EXPORT_DIR or both")
    session_factory = sessionmaker(create_engine(settings.database_url))
    logger.info(
        "delivering to %s every %ss",
        ", ".join(connector.name for connector in connectors),
        settings.delivery_seconds,
    )
    while True:
        for document_id, connector, outcome in deliver_due(
            session_factory, connectors, datetime.now(UTC)
        ):
            logger.info("document %s to %s: %s", document_id, connector, outcome)
        time.sleep(settings.delivery_seconds)


if __name__ == "__main__":
    main()
