"""Runtime configuration, read from the environment (see .env.example)."""

import os
from dataclasses import dataclass
from pathlib import Path

# Defaults match docker-compose.yml, which binds both services to localhost only.
DEFAULT_VALIDATOR_URL = "http://127.0.0.1:8081"
# Pinned so eval numbers refer to one model; change it deliberately and re-record.
DEFAULT_EXTRACTOR_MODEL = "claude-opus-5-5"
# PDFs longer than this go to a person unread (app/pipeline/document.py).
DEFAULT_MAX_PAGES = 10
DEFAULT_REDIS_URL = "redis://127.0.0.1:6380/0"
DEFAULT_DATABASE_URL = "postgresql+psycopg://einvoice:einvoice@127.0.0.1:5433/einvoice"


def _auth_mode(value: str) -> str:
    if value not in ("on", "off"):
        raise ValueError(f"AUTH must be on or off, not {value!r}")
    return value


@dataclass(frozen=True)
class OidcConfig:
    """The identity provider people sign in with (app/auth/oidc.py)."""

    issuer: str
    client_id: str
    client_secret: str
    # Where the provider sends the browser back; registered with the provider.
    redirect_uri: str
    # Providers that do not send `email_verified` (Entra ID) are refused unless
    # this is off — only for a directory whose emails its admin controls.
    require_verified_email: bool = True
    # What the sign-in button says, e.g. "Microsoft" or "Contoso"; None = a generic label.
    name: str | None = None


def _flag(value: str, name: str) -> bool:
    if value.lower() not in ("true", "false"):
        raise ValueError(f"{name} must be true or false, not {value!r}")
    return value.lower() == "true"


def _oidc(env: dict[str, str]) -> OidcConfig | None:
    """None without OIDC_ISSUER; with it, the rest must be there too."""
    issuer = env.get("OIDC_ISSUER") or None
    if issuer is None:
        return None
    needed = ("OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OIDC_REDIRECT_URI")
    missing = [name for name in needed if not env.get(name)]
    if missing:
        raise ValueError(f"OIDC_ISSUER is set, so {', '.join(missing)} must be too")
    return OidcConfig(
        issuer=issuer,
        client_id=env["OIDC_CLIENT_ID"],
        client_secret=env["OIDC_CLIENT_SECRET"],
        redirect_uri=env["OIDC_REDIRECT_URI"],
        require_verified_email=_flag(
            env.get("OIDC_REQUIRE_VERIFIED_EMAIL", "true"), "OIDC_REQUIRE_VERIFIED_EMAIL"
        ),
        name=env.get("OIDC_NAME") or None,
    )


@dataclass(frozen=True)
class Settings:
    validator_url: str
    database_url: str
    validator_timeout_seconds: float
    extractor_model: str
    extractor_effort: str | None
    # Only for EXTRACTOR_MODEL=ollama/…: where the local Ollama server listens.
    ollama_url: str
    extractor: str
    redis_url: str
    feedback_dir: Path
    ui_origin: str
    max_upload_bytes: int
    max_pages: int
    # Base URL of the ERP's read API; None = no ERP connected.
    erp_url: str | None = None
    erp_timeout_seconds: float = 5.0
    # How often the bank watch (app/watch.py) asks the ERP for new transfers.
    bank_watch_seconds: float = 300.0
    # How often decided invoices are delivered (app/deliver.py).
    delivery_seconds: float = 10.0
    # Folder for the DATEV-style booking lists; None = no CSV export.
    export_dir: str | None = None
    # How invoices get into the ERP: "api" (POST /booked-invoices) or, where
    # an ERP has no booking API, "form" (its HTML screens, with a browser).
    erp_channel: str = "api"
    # Where the ERP's screens are, if not at erp_url.
    erp_ui_url: str | None = None
    # Hosts the browser may reach; empty = only the host of the ERP's screens.
    browser_allowed_hosts: tuple[str, ...] = ()
    # Action logs and failure screenshots of the browser connector.
    browser_log_dir: str = "data/browser"
    # Receiver of signed bookings, and the shared secret; None = no webhook.
    webhook_url: str | None = None
    webhook_secret: str | None = None
    # "on": every request needs a signed-in user or an API token (v1.0).
    # "off": no sign-in, as before v1.0 — then only requests addressed to
    # these hosts are answered, so it cannot be used from another machine.
    auth: str = "on"
    open_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")
    # The identity provider people sign in with; None = only the built-in
    # password account(s) and API tokens.
    oidc: OidcConfig | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            validator_url=os.environ.get("VALIDATOR_URL", DEFAULT_VALIDATOR_URL),
            database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
            validator_timeout_seconds=float(os.environ.get("VALIDATOR_TIMEOUT_SECONDS", "60")),
            extractor_model=os.environ.get("EXTRACTOR_MODEL", DEFAULT_EXTRACTOR_MODEL),
            # Unset = the model's own default effort.
            extractor_effort=os.environ.get("EXTRACTOR_EFFORT") or None,
            ollama_url=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
            # llm | baseline | none
            extractor=os.environ.get("EXTRACTOR", "llm"),
            redis_url=os.environ.get("REDIS_URL", DEFAULT_REDIS_URL),
            # Review corrections of real documents: outside git (data/private is ignored).
            feedback_dir=Path(os.environ.get("FEEDBACK_DIR", "data/private/feedback")),
            ui_origin=os.environ.get("UI_ORIGIN", "http://localhost:3000"),
            max_upload_bytes=int(os.environ.get("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))),
            max_pages=int(os.environ.get("MAX_PAGES", str(DEFAULT_MAX_PAGES))),
            erp_url=os.environ.get("ERP_URL") or None,
            erp_timeout_seconds=float(os.environ.get("ERP_TIMEOUT_SECONDS", "5")),
            bank_watch_seconds=float(os.environ.get("BANK_WATCH_SECONDS", "300")),
            delivery_seconds=float(os.environ.get("DELIVERY_SECONDS", "10")),
            export_dir=os.environ.get("EXPORT_DIR") or None,
            erp_channel=os.environ.get("ERP_CHANNEL") or "api",
            erp_ui_url=os.environ.get("ERP_UI_URL") or None,
            browser_allowed_hosts=tuple(
                host.strip()
                for host in os.environ.get("BROWSER_ALLOWED_HOSTS", "").split(",")
                if host.strip()
            ),
            browser_log_dir=os.environ.get("BROWSER_LOG_DIR") or "data/browser",
            webhook_url=os.environ.get("WEBHOOK_URL") or None,
            webhook_secret=os.environ.get("WEBHOOK_SECRET") or None,
            auth=_auth_mode(os.environ.get("AUTH", "on")),
            oidc=_oidc(dict(os.environ)),
        )
