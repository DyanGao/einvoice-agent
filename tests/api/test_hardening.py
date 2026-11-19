"""Hardening of sign-in (v1.0 Stage 1, increment 4): a limit on failed
password sign-ins, Secure cookies behind HTTPS, and security headers."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api.main import SESSION_COOKIE, create_app
from app.auth import Role, create_user
from app.auth.limits import SignInLimiter
from tests.support import PDF, inline_queue, settings_for

ADMIN = {"email": "admin@example.com", "password": "first-setup-password"}
UI_ORIGIN = "http://localhost:3000"


def client_for(session_factory, tmp_path, **settings_changes) -> TestClient:
    settings = replace(settings_for(tmp_path), auth="on", ui_origin=UI_ORIGIN, **settings_changes)
    limiter = SignInLimiter(max_per_email=3, max_per_address=10)
    with session_factory.begin() as session:
        create_user(session, ADMIN["email"], "Admin", Role.ADMIN, ADMIN["password"])
    return TestClient(
        create_app(session_factory, inline_queue(session_factory), settings, limiter=limiter)
    )


@pytest.fixture
def client(session_factory, tmp_path):
    return client_for(session_factory, tmp_path)


def wrong(client):
    return client.post("/auth/login", json={**ADMIN, "password": "not-the-password"})


def test_after_too_many_wrong_passwords_even_the_right_one_waits(client):
    for _ in range(3):
        assert wrong(client).status_code == 401
    response = client.post("/auth/login", json=ADMIN)
    assert response.status_code == 429
    assert 0 < int(response.headers["Retry-After"]) <= 900
    assert "too many failed sign-ins" in response.json()["detail"]


def test_a_success_before_the_limit_starts_the_count_again(client):
    wrong(client)
    wrong(client)
    assert client.post("/auth/login", json=ADMIN).status_code == 200
    wrong(client)
    wrong(client)
    assert client.post("/auth/login", json=ADMIN).status_code == 200


def test_cookies_are_not_secure_over_plain_http_by_default(client):
    cookie = client.post("/auth/login", json=ADMIN).headers["set-cookie"]
    assert SESSION_COOKIE in cookie
    assert "Secure" not in cookie


def test_cookies_are_secure_behind_an_https_proxy_when_configured(session_factory, tmp_path):
    client = client_for(session_factory, tmp_path, cookie_secure="true")
    response = client.post("/auth/login", json=ADMIN)
    assert "Secure" in response.headers["set-cookie"]
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"


def test_cookies_are_secure_when_the_request_came_over_https(session_factory, tmp_path):
    client = client_for(session_factory, tmp_path)
    client.base_url = "https://testserver"
    response = client.post("/auth/login", json=ADMIN)
    assert "Secure" in response.headers["set-cookie"]
    assert "Strict-Transport-Security" in response.headers


def test_api_answers_carry_security_headers(client):
    headers = client.get("/health").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert "Strict-Transport-Security" not in headers


def test_refusals_carry_them_too(client):
    assert client.get("/documents").headers["X-Frame-Options"] == "DENY"


def test_the_original_document_may_be_framed_by_the_review_ui_only(client):
    token = client.post("/auth/login", json=ADMIN)
    assert token.status_code == 200
    ui = {"X-Requested-By": "einvoice-ui"}
    upload = client.post("/documents", files={"file": ("r.pdf", PDF)}, headers=ui)
    response = client.get(f"/documents/{upload.json()['id']}/content")
    assert response.headers["Content-Security-Policy"] == f"frame-ancestors {UI_ORIGIN}"
    assert "X-Frame-Options" not in response.headers


def test_the_api_docs_keep_their_scripts(client):
    assert "Content-Security-Policy" not in client.get("/docs").headers
