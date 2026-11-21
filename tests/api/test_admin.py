"""The admin pages' API (v1.0 Stage 2, increment 5): a tenant's admin
manages its members and the settings that are theirs to change; a person in
several tenants switches between them."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import db
from app.api.main import create_app
from app.auth import Role, add_membership, create_token, create_user
from tests.support import PDF, inline_queue, settings_for

ACME = "acme"
PASSWORD = "a-long-test-password"
UI = {"X-Requested-By": "einvoice-ui"}


@pytest.fixture
def client(session_factory, tmp_path):
    with session_factory.begin() as session:
        session.add(db.TenantRow(id=ACME, slug="acme", name="Acme GmbH"))
    settings = replace(settings_for(tmp_path), auth="on")
    return TestClient(create_app(session_factory, inline_queue(session_factory), settings))


def bearer(session_factory, email, role, tenant_id=db.DEFAULT_TENANT_ID) -> dict:
    with session_factory.begin() as session:
        user = create_user(session, email, email.split("@")[0].title(), role, tenant_id=tenant_id)
        return {"Authorization": f"Bearer {create_token(session, user.id, 't', tenant_id)}"}


@pytest.fixture
def admin(session_factory, client):
    return bearer(session_factory, "ada@example.com", Role.ADMIN)


def emails(client, headers) -> list[str]:
    response = client.get("/admin/members", headers=headers)
    assert response.status_code == 200, response.text
    return [m["email"] for m in response.json()["members"]]


def test_an_admin_sees_the_members_of_their_own_tenant_only(session_factory, client, admin):
    bearer(session_factory, "vera@example.com", Role.VIEWER)
    bearer(session_factory, "bob@acme.example", Role.ADMIN, ACME)
    assert emails(client, admin) == ["ada@example.com", "vera@example.com"]


def test_someone_new_is_added_and_can_then_sign_in_through_the_provider(
    session_factory, client, admin
):
    response = client.post(
        "/admin/members",
        headers=admin,
        json={"email": "Nina@Example.com", "name": "Nina", "role": "reviewer"},
    )
    assert response.status_code == 201
    assert response.json()["password"] is False
    assert "nina@example.com" in emails(client, admin)


def test_someone_from_another_tenant_gets_a_role_here_too(session_factory, client, admin):
    bearer(session_factory, "bob@acme.example", Role.ADMIN, ACME)
    response = client.post(
        "/admin/members",
        headers=admin,
        json={"email": "bob@acme.example", "name": "ignored", "role": "viewer"},
    )
    assert response.status_code == 201
    with session_factory() as session:
        roles = {
            m.tenant_id: m.role
            for m in session.query(db.MembershipRow).all()
            if m.user_id == response.json()["user_id"]
        }
    assert roles == {ACME: "admin", db.DEFAULT_TENANT_ID: "viewer"}
    again = client.post(
        "/admin/members",
        headers=admin,
        json={"email": "bob@acme.example", "name": "x", "role": "viewer"},
    )
    assert again.status_code == 409


@pytest.mark.parametrize("role", [Role.VIEWER, Role.REVIEWER])
def test_only_an_admin_may_manage_members_and_settings(session_factory, client, role):
    headers = bearer(session_factory, "someone@example.com", role)
    assert client.get("/admin/members", headers=headers).status_code == 403
    assert client.get("/admin/settings", headers=headers).status_code == 403


def test_a_role_is_changed(session_factory, client, admin):
    bearer(session_factory, "vera@example.com", Role.VIEWER)
    vera = next(
        m
        for m in client.get("/admin/members", headers=admin).json()["members"]
        if m["email"] == "vera@example.com"
    )
    response = client.patch(
        f"/admin/members/{vera['user_id']}", headers=admin, json={"role": "reviewer"}
    )
    assert response.json()["role"] == "reviewer"


def own_id(client, headers) -> str:
    members = client.get("/admin/members", headers=headers).json()["members"]
    return next(m["user_id"] for m in members if m["email"] == "ada@example.com")


def test_an_admin_cannot_demote_or_remove_themselves(client, admin):
    me = own_id(client, admin)
    demoted = client.patch(f"/admin/members/{me}", headers=admin, json={"role": "viewer"})
    removed = client.delete(f"/admin/members/{me}", headers=admin)
    assert (demoted.status_code, removed.status_code) == (409, 409)
    assert "own admin role" in demoted.json()["detail"]


def test_a_removed_members_token_stops_working_at_once(session_factory, client, admin):
    vera = bearer(session_factory, "vera@example.com", Role.REVIEWER)
    assert client.get("/documents", headers=vera).status_code == 200
    vera_id = next(
        m["user_id"]
        for m in client.get("/admin/members", headers=admin).json()["members"]
        if m["email"] == "vera@example.com"
    )
    assert client.delete(f"/admin/members/{vera_id}", headers=admin).status_code == 204
    assert client.get("/documents", headers=vera).status_code == 401


def test_a_member_of_another_tenant_cannot_be_touched(session_factory, client, admin):
    with session_factory.begin() as session:
        bob = create_user(session, "bob@acme.example", "Bob", Role.REVIEWER, tenant_id=ACME)
        bob_id = bob.id
    assert client.delete(f"/admin/members/{bob_id}", headers=admin).status_code == 404
    patched = client.patch(f"/admin/members/{bob_id}", headers=admin, json={"role": "admin"})
    assert patched.status_code == 404


def test_the_admin_changes_the_settings_that_are_theirs(client, admin):
    response = client.patch("/admin/settings", headers=admin, json={"changes": {"max_pages": 4}})
    assert response.status_code == 200, response.text
    assert response.json()["settings"]["max_pages"] == 4
    assert set(response.json()["editable"]) == {"max_pages", "auto_on_order_found_by_content"}


@pytest.mark.parametrize(
    "changes",
    [
        {"erp_url": "http://169.254.169.254/latest/meta-data"},
        {"export_dir": "/etc"},
        {"webhook_url": "https://attacker.example/hook"},
        {"webhook_secret_env": "TENANT_X"},
        {"browser_allowed_hosts": ["internal:22"]},
    ],
)
def test_settings_that_reach_the_servers_network_or_files_are_the_operators(client, admin, changes):
    response = client.patch("/admin/settings", headers=admin, json={"changes": changes})
    assert response.status_code == 403
    assert "operator" in response.json()["detail"]


def test_a_setting_that_cannot_work_is_refused(client, admin):
    response = client.patch("/admin/settings", headers=admin, json={"changes": {"max_pages": 0}})
    assert response.status_code == 422


def sign_in(client, email="eva@example.com") -> None:
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text


@pytest.fixture
def eva(session_factory, client):
    """In both tenants: reviewer in default, viewer in Acme."""
    with session_factory.begin() as session:
        user = create_user(session, "eva@example.com", "Eva", Role.REVIEWER, password=PASSWORD)
        add_membership(session, user.id, ACME, Role.VIEWER)
    sign_in(client)
    return client


def test_a_person_in_two_tenants_switches_between_them(eva):
    upload = eva.post("/documents", files={"file": ("r.pdf", PDF)}, headers=UI)
    assert upload.status_code == 202
    me = eva.get("/auth/me").json()
    assert [t["slug"] for t in me["tenants"]] == ["default", "acme"]
    switched = eva.post("/auth/tenant", headers=UI, json={"tenant_id": ACME})
    assert (switched.json()["tenant"]["slug"], switched.json()["role"]) == ("acme", "viewer")
    # Acme sees none of the default tenant's documents, and its role applies.
    assert eva.get("/documents").json()["documents"] == []
    assert eva.post("/documents", files={"file": ("r.pdf", PDF)}, headers=UI).status_code == 403


def test_no_switch_into_a_tenant_one_is_not_in(session_factory, eva):
    with session_factory.begin() as session:
        session.add(db.TenantRow(id="other", slug="other", name="Other"))
    response = eva.post("/auth/tenant", headers=UI, json={"tenant_id": "other"})
    assert response.status_code == 404


def test_switching_needs_the_ui_header(eva):
    assert eva.post("/auth/tenant", json={"tenant_id": ACME}).status_code == 403


def test_a_token_cannot_switch(session_factory, client):
    headers = bearer(session_factory, "bot@example.com", Role.REVIEWER)
    response = client.post("/auth/tenant", headers=headers, json={"tenant_id": ACME})
    assert response.status_code == 409


def test_with_sign_in_off_members_are_managed_with_the_cli(session_factory, tmp_path):
    app = create_app(session_factory, inline_queue(session_factory), settings_for(tmp_path))
    response = TestClient(app).get("/admin/members")
    assert response.status_code == 409
    assert "CLI" in response.json()["detail"]


def test_the_language_one_chooses_is_kept_for_the_next_sign_in(session_factory, eva):
    assert eva.get("/auth/me").json()["language"] is None
    chosen = eva.post("/auth/language", headers=UI, json={"language": "de"})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["language"] == "de"
    # Another browser: a new session for the same person.
    eva.cookies.clear()
    sign_in(eva)
    assert eva.get("/auth/me").json()["language"] == "de"


def test_only_a_language_of_the_ui_is_taken(eva):
    response = eva.post("/auth/language", headers=UI, json={"language": "fr"})
    assert response.status_code == 422
    assert eva.get("/auth/me").json()["language"] is None


def test_choosing_a_language_needs_the_ui_header(eva):
    assert eva.post("/auth/language", json={"language": "de"}).status_code == 403


def test_a_token_has_no_language_to_choose(session_factory, client):
    headers = bearer(session_factory, "bot@example.com", Role.REVIEWER)
    response = client.post("/auth/language", headers=headers, json={"language": "de"})
    assert response.status_code == 409
