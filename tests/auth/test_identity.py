"""Who a request comes from: users, passwords, API tokens and sessions."""

from datetime import UTC, datetime, timedelta

import pytest

from app import db
from app.auth import (
    Role,
    authenticate_session,
    authenticate_token,
    create_token,
    create_user,
    end_session,
    hash_password,
    revoke_token,
    sign_in,
    start_session,
    verify_password,
)

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)


def add(session_factory, email="anna@example.com", role=Role.REVIEWER, password=None):
    with session_factory.begin() as session:
        return create_user(session, email, "Anna Berg", role, password=password).id


def test_password_is_stored_as_a_salted_hash_that_verifies():
    stored = hash_password("correct horse battery staple")
    assert "correct horse" not in stored
    assert stored != hash_password("correct horse battery staple")  # salted
    assert verify_password("correct horse battery staple", stored)
    assert not verify_password("wrong", stored)


def test_roles_are_ordered():
    assert Role.ADMIN.allows(Role.REVIEWER)
    assert Role.REVIEWER.allows(Role.VIEWER)
    assert not Role.VIEWER.allows(Role.REVIEWER)


def test_email_is_unique_regardless_of_case(session_factory):
    add(session_factory, "Anna@Example.com")
    with pytest.raises(ValueError, match="already"):
        add(session_factory, "anna@example.com")


def test_token_is_shown_once_and_stored_only_as_a_hash(session_factory):
    user_id = add(session_factory)
    with session_factory.begin() as session:
        plain = create_token(session, user_id, "folder watch")
    with session_factory() as session:
        (row,) = session.query(db.ApiTokenRow).all()
        assert plain not in (row.token_hash, row.name)
        actor = authenticate_token(session, plain, NOW)
    assert (actor.email, actor.role, actor.verified) == ("anna@example.com", Role.REVIEWER, True)


def test_revoked_or_unknown_token_is_refused(session_factory):
    user_id = add(session_factory)
    with session_factory.begin() as session:
        plain = create_token(session, user_id, "script")
        assert authenticate_token(session, "eia_unknown", NOW) is None
        revoke_token(session, plain)
        assert authenticate_token(session, plain, NOW) is None


def test_disabled_user_cannot_use_a_token_or_sign_in(session_factory):
    user_id = add(session_factory, password="s3cret-pass-word")
    with session_factory.begin() as session:
        plain = create_token(session, user_id, "script")
        session.get(db.UserRow, user_id).disabled = True
    with session_factory.begin() as session:
        assert authenticate_token(session, plain, NOW) is None
        assert sign_in(session, "anna@example.com", "s3cret-pass-word") is None


def test_sign_in_with_the_right_password_only(session_factory):
    add(session_factory, password="s3cret-pass-word")
    with session_factory.begin() as session:
        assert sign_in(session, "ANNA@example.com", "s3cret-pass-word").email == "anna@example.com"
        assert sign_in(session, "anna@example.com", "nope") is None
        assert sign_in(session, "nobody@example.com", "s3cret-pass-word") is None


def test_user_without_a_password_cannot_sign_in_with_one(session_factory):
    add(session_factory)  # signs in through the identity provider
    with session_factory.begin() as session:
        assert sign_in(session, "anna@example.com", "") is None


def test_session_lasts_until_it_expires_or_ends(session_factory):
    user_id = add(session_factory)
    with session_factory.begin() as session:
        sid = start_session(session, user_id, NOW, lifetime=timedelta(hours=8))
        (row,) = session.query(db.SessionRow).all()
        assert sid != row.id_hash
    with session_factory.begin() as session:
        assert (
            authenticate_session(session, sid, NOW + timedelta(hours=7)).email == "anna@example.com"
        )
        assert authenticate_session(session, sid, NOW + timedelta(hours=9)) is None
        end_session(session, sid)
        assert authenticate_session(session, sid, NOW) is None
