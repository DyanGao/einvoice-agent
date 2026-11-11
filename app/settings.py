"""Runtime configuration, read from the environment (see .env.example)."""

import os
from dataclasses import dataclass

# Defaults match docker-compose.yml, which binds both services to localhost only.
DEFAULT_VALIDATOR_URL = "http://127.0.0.1:8081"
DEFAULT_DATABASE_URL = "postgresql+psycopg://einvoice:einvoice@127.0.0.1:5433/einvoice"


@dataclass(frozen=True)
class Settings:
    validator_url: str
    database_url: str
    validator_timeout_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            validator_url=os.environ.get("VALIDATOR_URL", DEFAULT_VALIDATOR_URL),
            database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
            validator_timeout_seconds=float(os.environ.get("VALIDATOR_TIMEOUT_SECONDS", "60")),
        )
