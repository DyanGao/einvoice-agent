"""Each tenant's own settings (v1.0 Stage 2, increment 4): which ERP its
invoices are matched against and booked into, where else bookings go, how
many pages a PDF may have, and one decision setting.

A tenant's settings live in `tenant_settings`: only what it set itself. The
rest comes from its base: `.env` for the default tenant (as before tenants
existed, so an installation with one company keeps working unchanged), the
field defaults for every other tenant. `einvoice tenant-set default
max_pages=5` changes that one setting; the default tenant's ERP still follows
`.env` until it is set too.

Secrets are never stored here. A tenant names the environment variable that
holds its webhook secret (`webhook_secret_env`, starting with TENANT_), and
the value stays in `.env` (owner's decision, 2026-11-20; encryption at rest
comes with v1.0 Stage 6).

Installation-wide on purpose: the model reading PDFs and its calibrated
confidence threshold (they belong to the model, not the company), the
validator, the database, Redis.
"""

import logging
import os
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.checks.service import Checker, build_checker, installation_decisions
from app.connectors.base import Connector
from app.db.tables import DEFAULT_TENANT_ID, TenantRow, TenantSettingsRow
from app.settings import DEFAULT_MAX_PAGES, Settings

logger = logging.getLogger(__name__)

_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
# Settings that name the environment variable holding a secret.
SECRET_NAMES = (
    "webhook_secret_env",
    "imap_password_env",
    "imap_oauth_secret_env",
    "imap_oauth_refresh_env",
)
# What a tenant's own admin may change in the admin UI. Everything else
# reaches the server's network or files (an ERP URL, a folder, a webhook) and
# is the installation operator's, through `einvoice tenant-set` (owner's
# decision, 2026-11-20).
TENANT_ADMIN_KEYS = ("max_pages", "auto_on_order_found_by_content")
# What a tenant may point at: never the installation's own secrets.
TENANT_SECRET_PREFIX = "TENANT_"  # noqa: S105 -- a name prefix, not a secret


class TenantConfig(BaseModel):
    """A tenant's settings. Every field may be left out: no ERP, no
    connectors, the default page limit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # The ERP's read API (matching, bank watch) and, with channel "api", its
    # booking API. None: no ERP; documents are checked without one.
    erp_url: str | None = None
    erp_timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    erp_channel: Literal["api", "form"] = "api"
    erp_ui_url: str | None = None
    browser_allowed_hosts: tuple[str, ...] = ()
    export_dir: str | None = None
    webhook_url: str | None = None
    # The NAME of the environment variable holding the webhook secret.
    webhook_secret_env: str | None = None
    max_pages: int = Field(default=DEFAULT_MAX_PAGES, ge=1, le=500)
    # An order found by the lines and amount alone may be booked on (D9).
    auto_on_order_found_by_content: bool = False
    # The mailbox invoices are sent to (app/mailbox.py). No host: no mailbox.
    imap_host: str | None = None
    imap_port: int = Field(default=993, ge=1, le=65535)
    # Implicit TLS (IMAPS). Without it only to this machine, for a test server.
    imap_tls: bool = True
    imap_user: str | None = None
    # The NAME of the environment variable holding the mailbox password.
    imap_password_env: str | None = None
    imap_folder: str = "INBOX"
    # Where a handled mail goes; None: it stays and is flagged. Never deleted.
    imap_processed_folder: str | None = None
    # How the poller signs in: a password, or OAuth2 (XOAUTH2) with a token
    # from Microsoft (the app signs in as itself: client credentials) or
    # Google (a refresh token from a one-time consent). Owner's decision,
    # 2026-11-20 (app/mailbox_oauth.py).
    imap_auth: Literal["password", "microsoft", "google"] = "password"
    # Microsoft Entra tenant (directory id or domain) of the mailbox.
    imap_oauth_tenant: str | None = None
    imap_oauth_client_id: str | None = None
    # The NAMES of the environment variables with the client secret and, for
    # Google, the refresh token.
    imap_oauth_secret_env: str | None = None
    imap_oauth_refresh_env: str | None = None
    # Where tokens come from, if not the provider's own endpoint (a test one).
    imap_oauth_token_url: str | None = None
    # Retention (v1.0 Stage 6, app/retention.py): years kept after the end of
    # the year an invoice is from, received in or booked in, whichever is last
    # (§ 147 AO: 8 since 2025 — to be confirmed with a tax adviser). Never fewer.
    retention_years: int = Field(default=8, ge=8, le=30)
    # Documents past their retention end are deleted only with this on.
    deletion_enabled: bool = False
    # Every document of the tenant kept, whatever its retention end (a tax
    # audit, a dispute, a court's order).
    legal_hold: bool = False

    @field_validator("erp_url", "erp_ui_url", "webhook_url", "imap_oauth_token_url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is not None and urlsplit(value).scheme not in ("http", "https"):
            raise ValueError(f"not an http(s) URL: {value!r}")
        return value

    @field_validator(
        "webhook_secret_env", "imap_password_env", "imap_oauth_secret_env", "imap_oauth_refresh_env"
    )
    @classmethod
    def _env_name(cls, value: str | None) -> str | None:
        if value is not None and not _ENV_NAME.fullmatch(value):
            raise ValueError(f"not an environment variable name: {value!r}")
        return value

    @model_validator(mode="after")
    def _mailbox(self) -> "TenantConfig":
        if self.imap_host is None:
            return self
        needed = {
            "password": ("imap_user", "imap_password_env"),
            "microsoft": (
                "imap_user",
                "imap_oauth_tenant",
                "imap_oauth_client_id",
                "imap_oauth_secret_env",
            ),
            "google": (
                "imap_user",
                "imap_oauth_client_id",
                "imap_oauth_secret_env",
                "imap_oauth_refresh_env",
            ),
        }[self.imap_auth]
        missing = [name for name in needed if getattr(self, name) is None]
        if missing:
            raise ValueError(
                f"a mailbox with imap_auth={self.imap_auth} needs {', '.join(missing)}"
            )
        if not self.imap_tls and self.imap_host not in _LOOPBACK:
            raise ValueError("a mailbox on another machine is only read over TLS (imap_tls)")
        return self

    @classmethod
    def from_settings(cls, settings: Settings) -> "TenantConfig":
        """The default tenant's settings as `.env` gives them."""
        return cls(
            erp_url=settings.erp_url,
            erp_timeout_seconds=settings.erp_timeout_seconds,
            erp_channel=settings.erp_channel,
            erp_ui_url=settings.erp_ui_url,
            browser_allowed_hosts=settings.browser_allowed_hosts,
            export_dir=settings.export_dir,
            webhook_url=settings.webhook_url,
            webhook_secret_env="WEBHOOK_SECRET" if settings.webhook_url else None,
            max_pages=settings.max_pages,
        )


def _base(tenant_id: str, settings: Settings) -> TenantConfig:
    """What a tenant has before it sets anything: .env for the default
    tenant, nothing for the others."""
    if tenant_id == DEFAULT_TENANT_ID:
        return TenantConfig.from_settings(settings)
    return TenantConfig()


def config_for(session: Session, tenant_id: str, settings: Settings) -> TenantConfig:
    """The tenant's settings: what it set itself, over its base. So the
    default tenant follows .env in everything it has not set."""
    row = session.get(TenantSettingsRow, tenant_id)
    base = _base(tenant_id, settings)
    if row is None or not row.config:
        return base
    return TenantConfig.model_validate({**base.model_dump(), **row.config})


def parse_assignments(assignments: list[str]) -> dict[str, object]:
    """`key=value` pairs from the command line; an empty value clears the key."""
    changes: dict[str, object] = {}
    for item in assignments:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ValueError(f"expected key=value, got {item!r}")
        if key not in TenantConfig.model_fields:
            raise ValueError(
                f"no setting {key!r}; settings: {', '.join(TenantConfig.model_fields)}"
            )
        if value == "":
            changes[key] = None
        elif key == "browser_allowed_hosts":
            changes[key] = tuple(host.strip() for host in value.split(",") if host.strip())
        else:
            changes[key] = value
    return changes


def update_config(
    session: Session, tenant_id: str, changes: dict[str, object], settings: Settings
) -> TenantConfig:
    """Set some of the tenant's settings; a key given as None is no longer
    set, so it follows the base again (.env for the default tenant). Only
    what was set is stored. The tenant's secret may only be named, and only
    as TENANT_…"""
    for key in SECRET_NAMES:
        secret = changes.get(key)
        if isinstance(secret, str) and not secret.startswith(TENANT_SECRET_PREFIX):
            raise ValueError(f"{key} must name a variable starting with {TENANT_SECRET_PREFIX}")
    row = session.get(TenantSettingsRow, tenant_id)
    own = dict(row.config) if row is not None else {}
    for key, value in changes.items():
        if value is None:
            own.pop(key, None)
        else:
            own[key] = value
    try:
        config = TenantConfig.model_validate({**_base(tenant_id, settings).model_dump(), **own})
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    # Stored as validated, and only the keys the tenant set.
    validated = config.model_dump(mode="json")
    own = {key: validated[key] for key in own}
    if row is None:
        session.add(TenantSettingsRow(tenant_id=tenant_id, config=own))
    else:
        row.config = own
    session.flush()
    return config


def create_tenant(session: Session, slug: str, name: str) -> TenantRow:
    """A new company, with empty settings: no ERP and no connectors until set."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", slug):
        raise ValueError("a tenant slug is lower-case letters, digits and -, at most 40")
    if session.scalar(select(TenantRow).where(TenantRow.slug == slug)) is not None:
        raise ValueError(f"a tenant {slug!r} exists already")
    tenant = TenantRow(slug=slug, name=name.strip())
    session.add(tenant)
    session.flush()
    session.add(TenantSettingsRow(tenant_id=tenant.id, config={}))
    session.flush()
    return tenant


def build_connectors(config: TenantConfig, browser_log_dir: str) -> list[Connector]:
    """Every downstream system the tenant is configured for. Raises
    ValueError if its settings cannot work (a missing secret, say)."""
    import httpx

    from app.connectors.csv_export import CsvExportConnector
    from app.connectors.erp_api import ErpApiConnector
    from app.connectors.erp_form import ErpFormConnector
    from app.connectors.webhook import WebhookConnector

    connectors: list[Connector] = []
    if config.erp_channel == "api" and config.erp_url is not None:
        client = httpx.Client(base_url=config.erp_url, timeout=config.erp_timeout_seconds)
        connectors.append(ErpApiConnector(client))
    if config.erp_channel == "form":
        ui_url = config.erp_ui_url or config.erp_url
        if ui_url is None:
            raise ValueError("erp_channel form needs erp_ui_url (or erp_url)")
        # Default: the ERP screens' own host and port, nothing else on that host.
        hosts = set(config.browser_allowed_hosts) or {urlsplit(ui_url).netloc}
        connectors.append(
            ErpFormConnector(ui_url, allowed_hosts=hosts, log_dir=Path(browser_log_dir))
        )
    if config.export_dir is not None:
        connectors.append(CsvExportConnector(Path(config.export_dir)))
    if config.webhook_url is not None:
        secret = os.environ.get(config.webhook_secret_env or "")
        if not secret:
            raise ValueError(
                f"a webhook URL is set, but its secret ({config.webhook_secret_env or 'no name'})"
                " is not in the environment: bookings are only sent signed"
            )
        connectors.append(
            WebhookConnector(config.webhook_url, secret, timeout=config.erp_timeout_seconds)
        )
    return connectors


class TenantServices:
    """What the worker, the API and the loops need per tenant, built from its
    settings and rebuilt when they change. Settings are read as the login
    role: the loops go through every tenant."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        checker_factory: Callable[[str | None, float], Checker] = build_checker,
        connector_factory: Callable[[TenantConfig, str], list[Connector]] | None = None,
    ) -> None:
        """The factories build a checker from an ERP URL and timeout, and the
        connectors from settings; tests give in-memory ones."""
        self._session_factory = session_factory
        self._settings = settings
        self._checker_factory = checker_factory
        self._connector_factory = connector_factory or build_connectors
        self._checkers: dict[tuple, Checker] = {}
        self._connectors: dict[str, list[Connector]] = {}

    def config(self, tenant_id: str) -> TenantConfig:
        with self._session_factory() as session:
            return config_for(session, tenant_id, self._settings)

    def tenant_ids(self) -> list[str]:
        with self._session_factory() as session:
            return list(session.scalars(select(TenantRow.id).order_by(TenantRow.created_at)))

    def checker(self, tenant_id: str) -> Checker:
        config = self.config(tenant_id)
        key = (config.erp_url, config.erp_timeout_seconds, config.auto_on_order_found_by_content)
        if key not in self._checkers:
            checker = self._checker_factory(config.erp_url, config.erp_timeout_seconds)
            decisions = replace(
                installation_decisions(),
                auto_on_order_found_by_content=config.auto_on_order_found_by_content,
            )
            self._checkers[key] = replace(checker, decisions=decisions)
        return self._checkers[key]

    def max_pages(self, tenant_id: str) -> int:
        return self.config(tenant_id).max_pages

    def connectors(self, tenant_id: str) -> list[Connector]:
        """The tenant's connectors; none (and a log line) if its settings
        cannot work, so one tenant's mistake does not stop the others."""
        config = self.config(tenant_id)
        key = config.model_dump_json()
        if key not in self._connectors:
            try:
                self._connectors[key] = self._connector_factory(
                    config, self._settings.browser_log_dir
                )
            except ValueError as exc:
                logger.error("tenant %s: connectors not configured: %s", tenant_id, exc)
                return []
        return self._connectors[key]
