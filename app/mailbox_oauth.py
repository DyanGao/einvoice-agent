"""OAuth2 access tokens for reading a mailbox with XOAUTH2 (v1.0 Stage 3,
increment 3). Microsoft 365 and Gmail are phasing out passwords for IMAP.

- Microsoft 365: the client-credentials grant. The application signs in as
  itself with its client id and secret; an Exchange admin grants it access to
  the one mailbox. Nothing rotates.
- Gmail: the refresh-token grant. A person consents once; the refresh token
  that gives is kept, like every secret, in the environment under a TENANT_
  name. Google does not replace it on use.

Owner's decision, 2026-11-20. A token is kept until shortly before it
expires and then fetched again. Secrets are read from the environment when a
token is needed; they never appear in a log or an error message.
"""

import os
import time
from collections.abc import Callable

import httpx

from app.tenants import TenantConfig

MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"  # noqa: S105
MICROSOFT_SCOPE = "https://outlook.office365.com/.default"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 -- endpoints, not secrets
# Fetched again this long before it would expire, so a slow round does not
# sign in with a token that runs out on the way.
REFRESH_MARGIN_SECONDS = 120


class TokenError(Exception):
    """No token: the message says why, without any secret in it."""


def _secret(name: str | None) -> str:
    value = os.environ.get(name or "")
    if not value:
        raise TokenError(f"{name or 'a secret'} is not set in the environment")
    return value


class TokenSource:
    """The tenant's current access token, fetched when there is none or it is
    about to expire. `http` and `clock` are injected so tests can stand in."""

    def __init__(
        self,
        config: TenantConfig,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if config.imap_auth not in ("microsoft", "google"):
            raise ValueError("a token source is for imap_auth microsoft or google")
        self._config = config
        self._http = http or httpx.Client(timeout=15.0)
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0

    def _request(self) -> tuple[str, dict[str, str]]:
        config = self._config
        if config.imap_auth == "microsoft":
            url = config.imap_oauth_token_url or MICROSOFT_TOKEN_URL.format(
                tenant=config.imap_oauth_tenant
            )
            return url, {
                "grant_type": "client_credentials",
                "client_id": config.imap_oauth_client_id or "",
                "client_secret": _secret(config.imap_oauth_secret_env),
                "scope": MICROSOFT_SCOPE,
            }
        return config.imap_oauth_token_url or GOOGLE_TOKEN_URL, {
            "grant_type": "refresh_token",
            "client_id": config.imap_oauth_client_id or "",
            "client_secret": _secret(config.imap_oauth_secret_env),
            "refresh_token": _secret(config.imap_oauth_refresh_env),
        }

    def token(self) -> str:
        if self._token is not None and self._clock() < self._expires_at - REFRESH_MARGIN_SECONDS:
            return self._token
        url, form = self._request()
        try:
            response = self._http.post(url, data=form, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise TokenError(f"{self._config.imap_auth}: token endpoint not reachable") from exc
        try:
            answer = response.json()
        except ValueError:
            answer = {}
        if response.status_code != 200 or not isinstance(answer.get("access_token"), str):
            # The provider's error code says what is wrong (invalid_client,
            # invalid_grant for a revoked consent, ...); nothing of the request.
            code = answer.get("error") if isinstance(answer, dict) else None
            raise TokenError(
                f"{self._config.imap_auth} refused a token: {code or response.status_code}"
            )
        self._token = answer["access_token"]
        self._expires_at = self._clock() + float(answer.get("expires_in") or 3600)
        return self._token


def xoauth2(user: str, token: str) -> bytes:
    """The SASL XOAUTH2 message IMAP's AUTHENTICATE expects."""
    return f"user={user}\x01auth=Bearer {token}\x01\x01".encode()
