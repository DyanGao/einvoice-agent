"""The delivery loop:  python -m app.deliver  (or `make deliver`).

Every DELIVERY_SECONDS it books, tenant by tenant, what has been approved or
decided `auto` and is not booked yet (app/delivery.py), in every system the
tenant is configured for (app/tenants.py): its ERP, through the ERP's API or
its screens; DATEV-style booking lists; a signed webhook. The default tenant
without settings of its own uses .env (ERP_URL, ERP_CHANNEL, EXPORT_DIR,
WEBHOOK_URL, WEBHOOK_SECRET).
Exactly once holds across restarts and crashes; run one loop per database
all the same, so that two do not send the same booking at the same moment.
"""

import logging
import time
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from app.connectors import Connector
from app.db.engine import database_engine
from app.db.instance_lock import AlreadyRunning, InstanceLock, LockLost
from app.db.tables import DEFAULT_TENANT_ID, TenantSettingsRow
from app.delivery import deliver_due
from app.settings import Settings
from app.tenants import TenantConfig, TenantServices
from app.tenants import build_connectors as tenant_connectors

logger = logging.getLogger(__name__)


def build_connectors(settings: Settings) -> list[Connector]:
    """The connectors .env configures (the default tenant's, unless it has
    settings of its own). Stops the loop from starting if they cannot work."""
    try:
        return tenant_connectors(TenantConfig.from_settings(settings), settings.browser_log_dir)
    except (ValueError, ValidationError) as exc:
        raise SystemExit(f"delivery not configured in .env: {exc}") from exc


def deliver_once(
    session_factory: sessionmaker, tenants: TenantServices, now: datetime
) -> list[tuple[str, str, str]]:
    """One round over every tenant with somewhere to deliver to."""
    done = []
    for tenant_id in tenants.tenant_ids():
        connectors = tenants.connectors(tenant_id)
        if connectors:
            done.extend(deliver_due(session_factory, connectors, now, tenant_id))
    return done


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings.from_env()
    engine = database_engine(settings.database_url)
    session_factory = sessionmaker(engine)
    with session_factory() as session:
        env_in_use = session.get(TenantSettingsRow, DEFAULT_TENANT_ID) is None
    if env_in_use:
        build_connectors(settings)
    try:
        # Two loops would race for the same due deliveries.
        lock = InstanceLock(engine, "delivery loop").acquire()
    except AlreadyRunning as exc:
        raise SystemExit(str(exc)) from exc
    tenants = TenantServices(session_factory, settings)
    for tenant_id in tenants.tenant_ids():
        names = ", ".join(c.name for c in tenants.connectors(tenant_id)) or "nothing"
        logger.info("tenant %s: delivering to %s", tenant_id, names)
    logger.info("every %ss", settings.delivery_seconds)
    while True:
        try:
            lock.check()
        except LockLost as exc:
            raise SystemExit(str(exc)) from exc
        for document_id, connector, outcome in deliver_once(
            session_factory, tenants, datetime.now(UTC)
        ):
            logger.info("document %s to %s: %s", document_id, connector, outcome)
        time.sleep(settings.delivery_seconds)


if __name__ == "__main__":
    main()
