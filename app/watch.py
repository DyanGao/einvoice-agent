"""The bank watch:  python -m app.watch  (or `make watch`).

The ERP cannot tell us when a transfer is booked, so this asks it every
BANK_WATCH_SECONDS for transfers not seen yet and matches the open invoices
of the suppliers they went to (app/rematch.py). One watch at a time per
database: two would act on the same transfers twice.
"""

import logging
import time

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.checks.service import Checker
from app.erp import ErpUnavailable, HttpErpClient
from app.rematch import BankData, watch_bank
from app.settings import Settings

logger = logging.getLogger(__name__)


def run_once(session_factory: sessionmaker[Session], erp: BankData, checker: Checker) -> list[str]:
    """One watch in one transaction: all of it is stored, or none."""
    with session_factory.begin() as session:
        return watch_bank(session, erp, checker)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request otherwise
    settings = Settings.from_env()
    if settings.erp_url is None:
        raise SystemExit("ERP_URL is not set: there is no ERP whose bank account could be watched")
    erp = HttpErpClient(
        httpx.Client(base_url=settings.erp_url, timeout=settings.erp_timeout_seconds)
    )
    session_factory = sessionmaker(create_engine(settings.database_url))
    checker = Checker(erp)
    logger.info("watching %s every %ss", settings.erp_url, settings.bank_watch_seconds)
    while True:
        try:
            new = run_once(session_factory, erp, checker)
            if new:
                logger.info("acted on %d new transfers", len(new))
        except ErpUnavailable as exc:
            # Nothing was recorded as seen: the next watch tries the same transfers.
            logger.warning("ERP not available, trying again later: %s", exc)
        time.sleep(settings.bank_watch_seconds)


if __name__ == "__main__":
    main()
