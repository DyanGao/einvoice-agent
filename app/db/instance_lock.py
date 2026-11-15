"""At most one running instance of a loop per database.

The bank watch and the delivery loop must each run once per database: two
watches would act on the same transfers twice, and two delivery loops would
race for the same due deliveries. A Postgres advisory lock, taken on its own
connection and held while the process runs, makes a second instance refuse
to start instead.

The lock lives as long as its connection. If the database restarts, the lock
is gone and another instance could take it; `check()` notices and the loop
stops, so the container's restart policy starts it again behind the lock.

Other databases have no such lock: a warning is logged and nothing is
enforced (the application runs on Postgres).
"""

import hashlib
import logging

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


class AlreadyRunning(RuntimeError):
    """Another instance holds the lock."""


class LockLost(RuntimeError):
    """The connection holding the lock is gone, and with it the lock."""


def lock_id(name: str) -> int:
    """A stable 64-bit key for the advisory lock."""
    digest = hashlib.sha256(f"einvoice:{name}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class InstanceLock:
    def __init__(self, engine: Engine, name: str) -> None:
        self._engine = engine
        self._name = name
        self._key = lock_id(name)
        self._connection: Connection | None = None

    @property
    def enforced(self) -> bool:
        return self._engine.dialect.name == "postgresql"

    def acquire(self) -> "InstanceLock":
        if not self.enforced:
            logger.warning(
                "%s: only one instance per database is enforced on Postgres, not on %s",
                self._name,
                self._engine.dialect.name,
            )
            return self
        connection = self._engine.connect()
        got = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": self._key}
        ).scalar_one()
        connection.commit()
        if not got:
            connection.close()
            raise AlreadyRunning(
                f"another {self._name} is already running on this database; "
                "only one may run at a time"
            )
        self._connection = connection
        return self

    def check(self) -> None:
        """Raise LockLost unless this connection still holds the lock.

        Asks Postgres rather than only pinging: a dropped connection can be
        replaced silently by a new one, which answers but holds no lock."""
        if self._connection is None:
            return
        try:
            held = self._connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                    "AND pid = pg_backend_pid() AND granted "
                    "AND classid = :high AND objid = :low AND objsubid = 1)"
                ),
                {"high": (self._key >> 32) & 0xFFFFFFFF, "low": self._key & 0xFFFFFFFF},
            ).scalar_one()
            self._connection.commit()
        except SQLAlchemyError as exc:
            raise LockLost(
                f"{self._name}: the database connection holding the lock is gone ({exc}); "
                "stopping so a restart takes the lock again"
            ) from exc
        if not held:
            raise LockLost(
                f"{self._name}: the lock is no longer held (the database connection was "
                "replaced); stopping so a restart takes the lock again"
            )

    def release(self) -> None:
        if self._connection is None:
            return
        try:
            self._connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self._key})
            self._connection.commit()
        except SQLAlchemyError as exc:
            # The connection is gone, and with it the lock: nothing left to release.
            logger.info("%s: lock connection already closed (%s)", self._name, exc)
        finally:
            self._connection.close()
            self._connection = None
