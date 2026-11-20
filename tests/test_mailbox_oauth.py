"""OAuth2 for reading a mailbox (v1.0 Stage 3, increment 3): tokens from
Microsoft (client credentials) and Google (refresh token), kept until shortly
before they expire, and no secret in any error."""

from urllib.parse import parse_qs

import httpx
import pytest

from app.mailbox import MailboxError, connect
from app.mailbox_oauth import (
    GOOGLE_TOKEN_URL,
    MICROSOFT_SCOPE,
    REFRESH_MARGIN_SECONDS,
    TokenError,
    TokenSource,
    xoauth2,
)
from app.tenants import TenantConfig

SECRET = "client-secret-for-tests"
REFRESH = "refresh-token-for-tests"

MICROSOFT = TenantConfig(
    imap_host="outlook.office365.com",
    imap_user="rechnungen@contoso.example",
    imap_auth="microsoft",
    imap_oauth_tenant="contoso.example",
    imap_oauth_client_id="einvoice-app",
    imap_oauth_secret_env="TENANT_TEST_OAUTH_SECRET",
)
GOOGLE = TenantConfig(
    imap_host="imap.gmail.com",
    imap_user="rechnungen@example.test",
    imap_auth="google",
    imap_oauth_client_id="einvoice-app.apps.googleusercontent.com",
    imap_oauth_secret_env="TENANT_TEST_OAUTH_SECRET",
    imap_oauth_refresh_env="TENANT_TEST_OAUTH_REFRESH",
)


@pytest.fixture(autouse=True)
def secrets(monkeypatch):
    monkeypatch.setenv("TENANT_TEST_OAUTH_SECRET", SECRET)
    monkeypatch.setenv("TENANT_TEST_OAUTH_REFRESH", REFRESH)


class Provider:
    """A token endpoint. Each token is new; `refuse` answers an OAuth error."""

    def __init__(self, refuse: str | None = None, expires_in: int = 3600) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.refuse = refuse
        self.expires_in = expires_in

    def handle(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.requests.append((str(request.url), form))
        if self.refuse:
            return httpx.Response(400, json={"error": self.refuse, "error_description": SECRET})
        return httpx.Response(
            200,
            json={"access_token": f"token-{len(self.requests)}", "expires_in": self.expires_in},
        )

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_microsoft_signs_the_app_in_with_client_credentials():
    provider = Provider()
    assert TokenSource(MICROSOFT, provider.http()).token() == "token-1"
    ((url, form),) = provider.requests
    assert url == "https://login.microsoftonline.com/contoso.example/oauth2/v2.0/token"
    assert form == {
        "grant_type": "client_credentials",
        "client_id": "einvoice-app",
        "client_secret": SECRET,
        "scope": MICROSOFT_SCOPE,
    }


def test_google_uses_the_refresh_token_from_the_one_time_consent():
    provider = Provider()
    TokenSource(GOOGLE, provider.http()).token()
    ((url, form),) = provider.requests
    assert url == GOOGLE_TOKEN_URL
    assert (form["grant_type"], form["refresh_token"]) == ("refresh_token", REFRESH)


def test_a_token_is_kept_until_shortly_before_it_expires():
    provider, clock = Provider(expires_in=3600), Clock()
    source = TokenSource(MICROSOFT, provider.http(), clock)
    assert source.token() == "token-1"
    clock.now += 3600 - REFRESH_MARGIN_SECONDS - 1
    assert source.token() == "token-1"
    clock.now += 2
    assert source.token() == "token-2"
    assert len(provider.requests) == 2


def test_a_refused_token_says_why_without_any_secret():
    source = TokenSource(GOOGLE, Provider(refuse="invalid_grant").http())
    with pytest.raises(TokenError) as refused:
        source.token()
    assert "invalid_grant" in str(refused.value)
    assert SECRET not in str(refused.value)
    assert REFRESH not in str(refused.value)


def test_a_missing_secret_is_named_not_guessed(monkeypatch):
    monkeypatch.delenv("TENANT_TEST_OAUTH_REFRESH")
    provider = Provider()
    with pytest.raises(TokenError, match="TENANT_TEST_OAUTH_REFRESH is not set"):
        TokenSource(GOOGLE, provider.http()).token()
    assert provider.requests == []


def test_an_unreachable_endpoint_is_a_token_error():
    def down(request):
        raise httpx.ConnectError("connection refused")

    source = TokenSource(MICROSOFT, httpx.Client(transport=httpx.MockTransport(down)))
    with pytest.raises(TokenError, match="not reachable"):
        source.token()


def test_a_token_problem_stops_only_this_mailbox():
    source = TokenSource(GOOGLE, Provider(refuse="invalid_client").http())
    with pytest.raises(MailboxError, match="invalid_client"):
        connect(GOOGLE, source)


def test_the_xoauth2_message_is_what_imap_expects():
    assert xoauth2("a@example.test", "t") == b"user=a@example.test\x01auth=Bearer t\x01\x01"


@pytest.mark.parametrize(
    ("changes", "missing"),
    [
        ({"imap_auth": "microsoft", "imap_oauth_tenant": None}, "imap_oauth_tenant"),
        ({"imap_auth": "google", "imap_oauth_refresh_env": None}, "imap_oauth_refresh_env"),
    ],
)
def test_oauth_settings_that_cannot_work_are_refused(changes, missing):
    base = (MICROSOFT if changes["imap_auth"] == "microsoft" else GOOGLE).model_dump()
    with pytest.raises(ValueError, match=missing):
        TenantConfig(**(base | changes))


def test_oauth_secrets_may_only_be_named_as_tenant_variables(session_factory, tmp_path):
    from app.settings import Settings
    from app.tenants import create_tenant, update_config

    with session_factory.begin() as session:
        acme = create_tenant(session, "acme", "Acme GmbH").id
        with pytest.raises(ValueError, match="TENANT_"):
            update_config(
                session, acme, {"imap_oauth_secret_env": "ANTHROPIC_API_KEY"}, Settings.from_env()
            )
