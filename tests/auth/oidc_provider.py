"""A stand-in identity provider for the sign-in tests, reached through
httpx.MockTransport: discovery, an authorization step the test plays as the
person, and a token endpoint that checks the code, PKCE and client secret."""

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import httpx

from app.settings import OidcConfig

ISSUER = "https://idp.example.test"
CONFIG = OidcConfig(
    issuer=ISSUER,
    client_id="einvoice",
    client_secret="client-secret-for-tests",
    redirect_uri="http://testserver/auth/oidc/callback",
)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def id_token(claims: dict) -> str:
    """Unsigned as far as the client cares: it trusts the TLS channel."""
    header = _b64(json.dumps({"alg": "RS256", "kid": "test"}).encode())
    return f"{header}.{_b64(json.dumps(claims).encode())}.{_b64(b'signature')}"


class FakeProvider:
    def __init__(self, config: OidcConfig = CONFIG) -> None:
        self.config = config
        self.metadata = {
            "issuer": config.issuer,
            "authorization_endpoint": f"{config.issuer}/authorize",
            "token_endpoint": f"{config.issuer}/token",
        }
        self._codes: dict[str, dict] = {}
        self.discovery_requests = 0

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle))

    def authorize(self, url: str, **claims) -> str:
        """The person signs in at the provider; returns the code it sends back.
        `claims` replace the defaults of the ID token."""
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
        assert query["client_id"] == self.config.client_id
        assert query["redirect_uri"] == self.config.redirect_uri
        assert query["code_challenge_method"] == "S256"
        code = secrets.token_urlsafe(16)
        self._codes[code] = {
            "challenge": query["code_challenge"],
            "claims": {
                "iss": self.config.issuer,
                "aud": self.config.client_id,
                "sub": "subject-1",
                "email": "anna@example.com",
                "email_verified": True,
                "name": "Anna Example",
                "nonce": query["nonce"],
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
            }
            | claims,
        }
        return code

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            self.discovery_requests += 1
            return httpx.Response(200, json=self.metadata)
        if request.url.path == "/token" and request.method == "POST":
            form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            grant = self._codes.pop(form.get("code", ""), None)
            if grant is None or form.get("client_secret") != self.config.client_secret:
                return httpx.Response(400, json={"error": "invalid_grant"})
            verifier = form.get("code_verifier", "")
            if _b64(hashlib.sha256(verifier.encode()).digest()) != grant["challenge"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"id_token": id_token(grant["claims"])})
        return httpx.Response(404)
