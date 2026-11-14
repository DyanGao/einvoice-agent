"""The delivery loop:  python -m app.deliver  (or `make deliver`).

Every DELIVERY_SECONDS it books what has been approved or decided `auto`
and is not booked yet (app/delivery.py), in every system configured: the
ERP, through its API (ERP_URL) or its screens (ERP_CHANNEL=form); the
DATEV-style booking lists (EXPORT_DIR); a signed webhook (WEBHOOK_URL,
WEBHOOK_SECRET).
Exactly once holds across restarts and crashes; run one loop per database
all the same, so that two do not send the same booking at the same moment.
"""

import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.connectors import (
    Connector,
    CsvExportConnector,
    ErpApiConnector,
    ErpFormConnector,
    WebhookConnector,
)
from app.delivery import deliver_due
from app.settings import Settings

logger = logging.getLogger(__name__)


def build_connectors(settings: Settings) -> list[Connector]:
    """Every downstream system this installation is configured for."""
    connectors: list[Connector] = []
    if settings.erp_channel not in ("api", "form"):
        raise SystemExit(f"ERP_CHANNEL must be api or form, not {settings.erp_channel!r}")
    if settings.erp_channel == "api" and settings.erp_url is not None:
        client = httpx.Client(base_url=settings.erp_url, timeout=settings.erp_timeout_seconds)
        connectors.append(ErpApiConnector(client))
    if settings.erp_channel == "form":
        ui_url = settings.erp_ui_url or settings.erp_url
        if ui_url is None:
            raise SystemExit("ERP_CHANNEL=form needs ERP_UI_URL (or ERP_URL)")
        hosts = set(settings.browser_allowed_hosts) or {urlsplit(ui_url).hostname}
        try:
            connectors.append(
                ErpFormConnector(
                    ui_url, allowed_hosts=hosts, log_dir=Path(settings.browser_log_dir)
                )
            )
        except ValueError as exc:
            raise SystemExit(f"browser connector not configured: {exc}") from exc
    if settings.export_dir is not None:
        connectors.append(CsvExportConnector(Path(settings.export_dir)))
    if settings.webhook_url is not None:
        if settings.webhook_secret is None:
            raise SystemExit(
                "WEBHOOK_URL is set but WEBHOOK_SECRET is not: bookings are only sent signed"
            )
        try:
            connectors.append(
                WebhookConnector(
                    settings.webhook_url,
                    settings.webhook_secret,
                    timeout=settings.erp_timeout_seconds,
                )
            )
        except ValueError as exc:
            raise SystemExit(f"webhook not configured: {exc}") from exc
    return connectors


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings.from_env()
    connectors = build_connectors(settings)
    if not connectors:
        raise SystemExit("nothing to deliver to: set ERP_URL, EXPORT_DIR or WEBHOOK_URL")
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
