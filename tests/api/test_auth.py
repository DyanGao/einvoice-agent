"""Sign-in and roles on the API (v1.0 Stage 1, increment 1)."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import db
from app.api.main import create_app
from app.auth import Role, create_token, create_user
from tests.support import PDF, inline_queue, settings_for

PASSWORD = "first-setup-password"
UI = {"X-Requested-By": "einvoice-ui"}


@pytest.fixture
def secured(session_factory, tmp_path):
    settings = replace(settings_for(tmp_path), auth="on")
    return TestClient(create_app(session_factory, inline_queue(session_factory), settings))


def token(session_factory, role: Role, email: str | None = None) -> dict:
    with session_factory.begin() as session:
        user = create_user(session, email or f"{role}@example.com", f"{role.title()} User", role)
        return {"Authorization": f"Bearer {create_token(session, user.id, 'test')}"}


def upload(client, headers) -> str:
    response = client.post("/documents", files={"file": ("r.pdf", PDF)}, headers=headers)
    assert response.status_code == 202, response.text
    return response.json()["id"]


OPEN = {
    "/health",
    "/",
    "/auth/login",
    "/auth/logout",
    "/auth/options",
    "/auth/oidc/start",
    "/auth/oidc/callback",
    "/openapi.json",
    "/docs",
    "/redoc",
    "/docs/oauth2-redirect",
}


def test_every_route_but_the_open_ones_needs_a_signed_in_user(secured):
    checked = 0
    for route in secured.app.routes:
        if route.path in OPEN:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            path = route.path.replace("{document_id}", "0" * 32)
            response = secured.request(method, path)
            assert response.status_code == 401, (method, path, response.status_code)
            checked += 1
    assert checked >= 12


def test_viewer_reads_but_cannot_change_anything(secured, session_factory):
    reviewer, viewer = token(session_factory, Role.REVIEWER), token(session_factory, Role.VIEWER)
    document_id = upload(secured, reviewer)
    assert secured.get(f"/documents/{document_id}", headers=viewer).status_code == 200
    assert secured.post(f"/documents/{document_id}/approve", headers=viewer).status_code == 403
    assert (
        secured.post("/documents", files={"file": ("r.pdf", PDF)}, headers=viewer).status_code
        == 403
    )


def test_audit_trail_names_the_signed_in_user(secured, session_factory):
    reviewer = token(session_factory, Role.REVIEWER, "anna@example.com")
    document_id = upload(secured, reviewer)
    secured.post(f"/documents/{document_id}/reject", headers=reviewer, json={"reason": "duplicate"})
    with session_factory() as session:
        user = session.query(db.UserRow).filter_by(email="anna@example.com").one()
        event = session.get(db.DocumentRow, document_id).audit_events[-1]
    assert (event.event, event.actor, event.actor_user_id) == ("rejected", "Reviewer User", user.id)


def test_typed_reviewer_name_is_ignored_when_signed_in(secured, session_factory):
    reviewer = token(session_factory, Role.REVIEWER)
    document_id = upload(secured, reviewer)
    secured.post(
        f"/documents/{document_id}/reject",
        headers=reviewer | {"X-Reviewer": "Someone Else"},
        json={"reason": "duplicate"},
    )
    detail = secured.get(f"/documents/{document_id}", headers=reviewer).json()
    assert detail["audit"][-1]["actor"] == "Reviewer User"


def test_password_sign_in_gives_a_session_cookie(secured, session_factory):
    with session_factory.begin() as session:
        create_user(session, "admin@example.com", "Admin", Role.ADMIN, password=PASSWORD)
    wrong = secured.post("/auth/login", json={"email": "admin@example.com", "password": "nope"})
    assert wrong.status_code == 401
    response = secured.post(
        "/auth/login", json={"email": "admin@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200
    cookie = response.cookies.get("eia_session")
    assert cookie
    me = secured.get("/auth/me")
    assert (me.status_code, me.json()["email"], me.json()["role"]) == (
        200,
        "admin@example.com",
        "admin",
    )


def test_a_change_with_the_cookie_needs_the_ui_header(secured, session_factory):
    """CSRF: another site can make the browser send the cookie, but not this header."""
    with session_factory.begin() as session:
        create_user(session, "admin@example.com", "Admin", Role.ADMIN, password=PASSWORD)
    secured.post("/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    without = secured.post("/documents", files={"file": ("r.pdf", PDF)})
    with_header = secured.post("/documents", files={"file": ("r.pdf", PDF)}, headers=UI)
    assert (without.status_code, with_header.status_code) == (403, 202)


def test_sign_out_ends_the_session(secured, session_factory):
    with session_factory.begin() as session:
        create_user(session, "admin@example.com", "Admin", Role.ADMIN, password=PASSWORD)
    secured.post("/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    assert secured.post("/auth/logout", headers=UI).status_code == 204
    assert secured.get("/auth/me").status_code == 401


# --- AUTH=off: today's open localhost mode -----------------------------------


@pytest.fixture
def open_client(session_factory, tmp_path):
    settings = replace(settings_for(tmp_path), auth="off")
    return TestClient(
        create_app(session_factory, inline_queue(session_factory), settings),
        base_url="http://localhost:8000",
    )


def test_without_sign_in_only_requests_addressed_to_localhost_are_answered(open_client):
    assert open_client.get("/documents").status_code == 200
    elsewhere = open_client.get("/documents", headers={"Host": "invoices.example.com"})
    assert elsewhere.status_code == 403
    assert "AUTH=off" in elsewhere.text


def test_without_sign_in_the_typed_name_is_recorded_as_unverified(open_client, session_factory):
    document_id = open_client.post("/documents", files={"file": ("r.pdf", PDF)}).json()["id"]
    open_client.post(
        f"/documents/{document_id}/reject",
        headers={"X-Reviewer": "anna"},
        json={"reason": "duplicate"},
    )
    with session_factory() as session:
        event = session.get(db.DocumentRow, document_id).audit_events[-1]
    assert (event.actor, event.actor_user_id) == ("anna", None)


def test_me_says_sign_in_is_off(open_client):
    assert open_client.get("/auth/me").json() == {"auth": "off"}
