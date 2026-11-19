"""The browser's way through the identity provider: start, provider, callback,
signed in — or back to the UI saying why not."""

from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.api.main import LOGIN_COOKIE, SESSION_COOKIE, create_app
from app.auth import Role, create_user
from app.auth.oidc import OidcClient
from tests.auth.oidc_provider import CONFIG, FakeProvider
from tests.support import inline_queue, settings_for

UI_ORIGIN = "http://localhost:3000"


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def client(session_factory, tmp_path, provider):
    settings = replace(settings_for(tmp_path), auth="on", oidc=CONFIG, ui_origin=UI_ORIGIN)
    app = create_app(
        session_factory,
        inline_queue(session_factory),
        settings,
        oidc=OidcClient(CONFIG, provider.http()),
    )
    return TestClient(app, follow_redirects=False)


def add_user(session_factory, email="anna@example.com", role=Role.REVIEWER):
    with session_factory.begin() as session:
        create_user(session, email, "Anna Berg", role)


def start(client) -> str:
    response = client.get("/auth/oidc/start")
    assert response.status_code == 303
    return response.headers["location"]


def sign_in(client, provider, **claims):
    target = start(client)
    code = provider.authorize(target, **claims)
    state = parse_qs(urlsplit(target).query)["state"][0]
    return client.get("/auth/oidc/callback", params={"code": code, "state": state})


def test_start_sends_the_browser_to_the_provider_with_pkce(client):
    target = start(client)
    query = parse_qs(urlsplit(target).query)
    assert target.startswith("https://idp.example.test/authorize?")
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge_method"] == ["S256"]
    assert LOGIN_COOKIE in client.cookies


def test_a_user_an_admin_added_is_signed_in_and_sent_to_the_ui(client, provider, session_factory):
    add_user(session_factory)
    response = sign_in(client, provider)
    assert response.headers["location"] == f"{UI_ORIGIN}/"
    me = client.get("/auth/me")
    assert me.json() == {
        "auth": "on",
        "name": "Anna Berg",
        "email": "anna@example.com",
        "role": "reviewer",
    }


def test_the_session_cookie_is_http_only_and_strict(client, provider, session_factory):
    add_user(session_factory)
    cookies = sign_in(client, provider).headers.get_list("set-cookie")
    session = next(cookie for cookie in cookies if cookie.startswith(SESSION_COOKIE))
    assert "HttpOnly" in session
    assert "SameSite=strict" in session
    # The login cookie has done its job.
    assert any(cookie.startswith(f'{LOGIN_COOKIE}=""') for cookie in cookies)


def test_someone_nobody_added_is_sent_back_refused(client, provider):
    response = sign_in(client, provider)
    assert response.headers["location"] == f"{UI_ORIGIN}/?signin=unknown_user"
    assert SESSION_COOKIE not in client.cookies
    assert client.get("/auth/me").status_code == 401


def test_an_unverified_email_is_sent_back_refused(client, provider, session_factory):
    add_user(session_factory)
    response = sign_in(client, provider, email_verified=False)
    assert response.headers["location"].endswith("?signin=unverified_email")


def test_a_callback_with_another_state_fails(client, provider, session_factory):
    add_user(session_factory)
    code = provider.authorize(start(client))
    response = client.get("/auth/oidc/callback", params={"code": code, "state": "forged"})
    assert response.headers["location"].endswith("?signin=failed")
    assert SESSION_COOKIE not in client.cookies


def test_a_callback_without_the_login_cookie_fails(client, provider, session_factory):
    """Login CSRF: someone else's code, pushed into this browser."""
    add_user(session_factory)
    target = start(client)
    code = provider.authorize(target)
    state = parse_qs(urlsplit(target).query)["state"][0]
    client.cookies.clear()
    response = client.get("/auth/oidc/callback", params={"code": code, "state": state})
    assert response.headers["location"].endswith("?signin=failed")


def test_an_id_token_for_another_sign_in_fails(client, provider, session_factory):
    add_user(session_factory)
    response = sign_in(client, provider, nonce="replayed")
    assert response.headers["location"].endswith("?signin=failed")


def test_cancelling_at_the_provider_goes_back_to_the_ui(client):
    start(client)
    response = client.get("/auth/oidc/callback", params={"error": "access_denied"})
    assert response.headers["location"].endswith("?signin=cancelled")


def test_without_a_provider_there_is_nothing_to_start(session_factory, tmp_path):
    settings = replace(settings_for(tmp_path), auth="on")
    app = create_app(session_factory, inline_queue(session_factory), settings)
    assert TestClient(app).get("/auth/oidc/start").status_code == 404


def test_with_sign_in_off_there_is_nothing_to_start(session_factory, tmp_path):
    app = create_app(
        session_factory, inline_queue(session_factory), replace(settings_for(tmp_path), oidc=CONFIG)
    )
    assert TestClient(app).get("/auth/oidc/start").status_code == 409


def test_the_sign_in_page_is_told_to_offer_the_provider(session_factory, tmp_path, provider):
    config = replace(CONFIG, name="Contoso")
    settings = replace(settings_for(tmp_path), auth="on", oidc=config)
    app = create_app(
        session_factory,
        inline_queue(session_factory),
        settings,
        oidc=OidcClient(config, provider.http()),
    )
    assert TestClient(app).get("/auth/options").json() == {
        "auth": "on",
        "provider": True,
        "provider_name": "Contoso",
    }


def test_without_a_provider_the_sign_in_page_offers_only_the_password(session_factory, tmp_path):
    settings = replace(settings_for(tmp_path), auth="on")
    app = create_app(session_factory, inline_queue(session_factory), settings)
    assert TestClient(app).get("/auth/options").json() == {
        "auth": "on",
        "provider": False,
        "provider_name": None,
    }


def test_with_sign_in_off_the_ui_is_told_so(session_factory, tmp_path):
    app = create_app(session_factory, inline_queue(session_factory), settings_for(tmp_path))
    assert TestClient(app).get("/auth/options").json() == {"auth": "off"}
