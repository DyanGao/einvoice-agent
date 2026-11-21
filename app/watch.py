"""The bank watch:  python -m app.watch  (or `make watch`).

The ERP cannot tell us when a transfer is booked, so this asks every
tenant's ERP (app/tenants.py) every BANK_WATCH_SECONDS for transfers not seen
yet and matches that tenant's open invoices of the suppliers they went to
(app/rematch.py). One watch at a time per
database: two would act on the same transfers twice, so a second one refuses
to start (app/db/instance_lock.py).
"""

import logging
import time

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app import crypto
from app.checks.service import Checker
from app.db import DEFAULT_TENANT_ID
from app.db.engine import database_engine
from app.db.instance_lock import AlreadyRunning, InstanceLock, LockLost
from app.db.rls import tenant_transaction
from app.erp import ErpUnavailable, HttpErpClient
from app.rematch import BankData, watch_bank
from app.settings import Settings
from app.tenants import TenantServices

logger = logging.getLogger(__name__)


def run_once(
    session_factory: sessionmaker[Session],
    erp: BankData,
    checker: Checker,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> list[str]:
    """One watch of the tenant's ERP in one transaction acting for the tenant:
    all of it is stored, or none."""
    with tenant_transaction(session_factory, tenant_id) as session:
        return watch_bank(session, erp, checker, tenant_id)


def watch_once(
    session_factory: sessionmaker[Session], tenants: TenantServices, erps: dict[str, BankData]
) -> dict[str, list[str]]:
    """One watch of every tenant that has an ERP: the new transfers acted on,
    per tenant. One ERP not answering does not keep the others waiting."""
    acted: dict[str, list[str]] = {}
    for tenant_id in tenants.tenant_ids():
        config = tenants.config(tenant_id)
        if config.erp_url is None:
            continue
        key = f"{config.erp_url} {config.erp_timeout_seconds}"
        if key not in erps:
            erps[key] = HttpErpClient(
                httpx.Client(base_url=config.erp_url, timeout=config.erp_timeout_seconds)
            )
        try:
            acted[tenant_id] = run_once(
                session_factory, erps[key], tenants.checker(tenant_id), tenant_id
            )
        except ErpUnavailable as exc:
            # Nothing was recorded as seen: the next watch tries the same transfers.
            logger.warning("tenant %s: ERP not available, trying again later: %s", tenant_id, exc)
    return acted


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request otherwise
    settings = Settings.from_env()
    engine = database_engine(settings.database_url)
    crypto.configure_from_env(engine)  # refuses to start without MASTER_KEY
    try:
        lock = InstanceLock(engine, "bank watch").acquire()
    except AlreadyRunning as exc:
        raise SystemExit(str(exc)) from exc
    session_factory = sessionmaker(engine)
    tenants = TenantServices(session_factory, settings)
    erps: dict[str, BankData] = {}
    logger.info("watching every tenant's ERP every %ss", settings.bank_watch_seconds)
    while True:
        try:
            lock.check()
        except LockLost as exc:
            raise SystemExit(str(exc)) from exc
        for tenant_id, new in watch_once(session_factory, tenants, erps).items():
            if new:
                logger.info("tenant %s: acted on %d new transfers", tenant_id, len(new))
        time.sleep(settings.bank_watch_seconds)


if __name__ == "__main__":
    main()
