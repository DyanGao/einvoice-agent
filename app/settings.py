"""Runtime configuration, read from the environment (see .env.example)."""

import os
from dataclasses import dataclass
from pathlib import Path

# Defaults match docker-compose.yml, which binds both services to localhost only.
DEFAULT_VALIDATOR_URL = "http://127.0.0.1:8081"
# Pinned so eval numbers refer to one model; change it deliberately and re-record.
DEFAULT_EXTRACTOR_MODEL = "claude-opus-5-5"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6380/0"
DEFAULT_DATABASE_URL = "postgresql+psycopg://einvoice:einvoice@127.0.0.1:5433/einvoice"


@dataclass(frozen=True)
class Settings:
    validator_url: str
    database_url: str
    validator_timeout_seconds: float
    extractor_model: str
    extractor_effort: str | None
    extractor: str
    redis_url: str
    feedback_dir: Path
    ui_origin: str
    max_upload_bytes: int
    # Base URL of the ERP's read API; None = no ERP connected.
    erp_url: str | None = None
    erp_timeout_seconds: float = 5.0
    # How often the bank watch (app/watch.py) asks the ERP for new transfers.
    bank_watch_seconds: float = 300.0
    # How often decided invoices are delivered (app/deliver.py).
    delivery_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            validator_url=os.environ.get("VALIDATOR_URL", DEFAULT_VALIDATOR_URL),
            database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
            validator_timeout_seconds=float(os.environ.get("VALIDATOR_TIMEOUT_SECONDS", "60")),
            extractor_model=os.environ.get("EXTRACTOR_MODEL", DEFAULT_EXTRACTOR_MODEL),
            # Unset = the model's own default effort.
            extractor_effort=os.environ.get("EXTRACTOR_EFFORT") or None,
            # llm | baseline | none
            extractor=os.environ.get("EXTRACTOR", "llm"),
            redis_url=os.environ.get("REDIS_URL", DEFAULT_REDIS_URL),
            # Review corrections of real documents: outside git (data/private is ignored).
            feedback_dir=Path(os.environ.get("FEEDBACK_DIR", "data/private/feedback")),
            ui_origin=os.environ.get("UI_ORIGIN", "http://localhost:3000"),
            max_upload_bytes=int(os.environ.get("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))),
            erp_url=os.environ.get("ERP_URL") or None,
            erp_timeout_seconds=float(os.environ.get("ERP_TIMEOUT_SECONDS", "5")),
            bank_watch_seconds=float(os.environ.get("BANK_WATCH_SECONDS", "300")),
            delivery_seconds=float(os.environ.get("DELIVERY_SECONDS", "10")),
        )
