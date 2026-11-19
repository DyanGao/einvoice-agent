"""Sign-in through an OpenID Connect provider (v1.0 Stage 1, increment 2).

The authorization code flow with PKCE, for Microsoft Entra ID, Google,
Keycloak or any provider that publishes `/.well-known/openid-configuration`:

1. `/auth/oidc/start` makes a state, a nonce and a PKCE verifier, keeps them in
   a short-lived cookie and sends the browser to the provider.
2. The provider sends the browser back to `/auth/oidc/callback` with a code.
   The state must match the cookie; the code is exchanged, with the verifier
   and the client secret, for an ID token.
3. The ID token's issuer, audience, expiry and nonce are checked, and the
   person is matched to a user an admin set up beforehand.

The ID token's signature is not checked. It comes straight from the
provider's token endpoint over HTTPS, which OpenID Connect Core 1.0 (section
3.1.3.7) accepts in place of the signature for this flow — so the token
endpoint must be HTTPS (plain HTTP only on loopback, for a local test
provider), and no ID token is ever taken from the browser.
"""

import base64
import binascii
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from app.settings import OidcConfig

SCOPES = "openid email profile"
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
# Clocks of the provider and this server may differ a little.
CLOCK_SKEW_SECONDS = 60


class OidcError(Exception):
    """The sign-in failed; the message says why, for the log (not for the user)."""


@dataclass(frozen=True)
class LoginAttempt:
    """What the browser carries from start to callback, in a cookie."""

    state: str
    nonce: str
    verifier: str

    @classmethod
    def new(cls) -> "LoginAttempt":
        return cls(*(secrets.token_urlsafe(32) for _ in range(3)))

    def cookie(self) -> str:
        # token_urlsafe never contains ".".
        return f"{self.state}.{self.nonce}.{self.verifier}"

    @classmethod
    def from_cookie(cls, value: str | None) -> "LoginAttempt | None":
        parts = (value or "").split(".")
        if len(parts) != 3 or not all(parts):
            return None
        return cls(*parts)

    def challenge(self) -> str:
        """PKCE S256: the provider gets the hash, the token request the verifier."""
        digest = hashlib.sha256(self.verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class Identity:
    """Who the provider says signed in."""

    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    name: str | None


def _require_https(url: str, what: str) -> None:
    parts = urlsplit(url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in _LOOPBACK):
        return
    raise OidcError(f"the provider's {what} must be https: {url}")


def _payload(id_token: str) -> dict[str, Any]:
    try:
        _, body, _ = id_token.split(".")
        padded = body + "=" * (-len(body) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, binascii.Error) as exc:
        raise OidcError("the ID token is not a JWT") from exc
    if not isinstance(claims, dict):
        raise OidcError("the ID token's payload is not an object")
    return claims


def check_claims(
    claims: dict[str, Any], *, issuer: str, client_id: str, nonce: str, now: datetime
) -> Identity:
    """The checks of OpenID Connect Core 3.1.3.7 that apply without a signature."""
    if claims.get("iss") != issuer:
        raise OidcError(f"ID token from another issuer: {claims.get('iss')!r}")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if client_id not in audiences:
        raise OidcError("ID token for another client")
    if len(audiences) > 1 and claims.get("azp") != client_id:
        raise OidcError("ID token with several audiences, but not authorized for this client")
    expires = claims.get("exp")
    if not isinstance(expires, int | float) or expires + CLOCK_SKEW_SECONDS < now.timestamp():
        raise OidcError("ID token expired")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OidcError("ID token for another sign-in (nonce)")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise OidcError("ID token without a subject")
    email = claims.get("email")
    return Identity(
        issuer=issuer,
        subject=subject,
        email=email.strip().lower() if isinstance(email, str) and email.strip() else None,
        # Some providers send "true" as a string.
        email_verified=claims.get("email_verified") in (True, "true"),
        name=claims.get("name") if isinstance(claims.get("name"), str) else None,
    )


class OidcClient:
    """Talks to one provider. `http` is injected so tests can stand in for it."""

    def __init__(self, config: OidcConfig, http: httpx.Client | None = None) -> None:
        self.config = config
        self._http = http or httpx.Client(timeout=10.0)
        self._metadata: dict[str, Any] | None = None

    def metadata(self) -> dict[str, Any]:
        """The provider's discovery document, fetched once."""
        if self._metadata is None:
            url = self.config.issuer.rstrip("/") + "/.well-known/openid-configuration"
            _require_https(url, "issuer")
            try:
                response = self._http.get(url)
                response.raise_for_status()
                metadata = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise OidcError(f"cannot read the provider's configuration: {exc}") from exc
            if metadata.get("issuer") != self.config.issuer:
                raise OidcError(f"the provider calls itself {metadata.get('issuer')!r}")
            for key in ("authorization_endpoint", "token_endpoint"):
                if not isinstance(metadata.get(key), str):
                    raise OidcError(f"the provider's configuration has no {key}")
            _require_https(metadata["token_endpoint"], "token endpoint")
            self._metadata = metadata
        return self._metadata

    def authorization_url(self, attempt: LoginAttempt) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "scope": SCOPES,
                "state": attempt.state,
                "nonce": attempt.nonce,
                "code_challenge": attempt.challenge(),
                "code_challenge_method": "S256",
            }
        )
        endpoint = self.metadata()["authorization_endpoint"]
        return f"{endpoint}{'&' if '?' in endpoint else '?'}{query}"

    def identify(self, code: str, attempt: LoginAttempt, now: datetime) -> Identity:
        """Exchange the code for an ID token and check it."""
        try:
            response = self._http.post(
                self.metadata()["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.config.redirect_uri,
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "code_verifier": attempt.verifier,
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            id_token = response.json().get("id_token")
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise OidcError(f"the provider did not exchange the code: {exc}") from exc
        if not isinstance(id_token, str):
            raise OidcError("the provider's answer has no ID token")
        return check_claims(
            _payload(id_token),
            issuer=self.config.issuer,
            client_id=self.config.client_id,
            nonce=attempt.nonce,
            now=now,
        )
