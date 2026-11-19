"""Sign-in through an identity provider: ID token checks, PKCE, and matching
the person to a user an admin added (v1.0 Stage 1, increment 2)."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.auth import Role, SignInRefused, create_user, sign_in_with_provider
from app.auth.oidc import LoginAttempt, OidcClient, OidcError, check_claims
from tests.auth.oidc_provider import CONFIG, ISSUER, FakeProvider

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
CLAIMS = {
    "iss": ISSUER,
    "aud": "einvoice",
    "sub": "subject-1",
    "email": "Anna@Example.com",
    "email_verified": True,
    "nonce": "n-1",
    "exp": NOW.timestamp() + 300,
}


def check(**changes):
    return check_claims(CLAIMS | changes, issuer=ISSUER, client_id="einvoice", nonce="n-1", now=NOW)


def test_a_good_id_token_names_the_person():
    identity = check()
    assert (identity.subject, identity.email, identity.email_verified) == (
        "subject-1",
        "anna@example.com",
        True,
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"iss": "https://evil.example"}, "another issuer"),
        ({"aud": "someone-else"}, "another client"),
        ({"aud": ["einvoice", "other"]}, "several audiences"),
        ({"exp": NOW.timestamp() - 120}, "expired"),
        ({"nonce": "n-2"}, "nonce"),
        ({"sub": ""}, "subject"),
    ],
)
def test_an_id_token_that_is_not_for_this_sign_in_is_refused(changes, message):
    with pytest.raises(OidcError, match=message):
        check(**changes)


def test_several_audiences_are_fine_when_authorized_for_this_client():
    assert check(aud=["einvoice", "other"], azp="einvoice").subject == "subject-1"


def test_email_verified_as_a_string_counts():
    assert check(email_verified="true").email_verified is True
    assert check(email_verified="false").email_verified is False


def test_pkce_challenge_is_s256_of_the_verifier():
    # Expected value computed with openssl: sha256, base64url, no padding.
    attempt = LoginAttempt("s", "n", "dBjftJeZ4CVP-mJ92IqyrW0ekdUvxVCMvqgLSM8q7Tg")
    assert attempt.challenge() == "k0HXLdHlT6Gp-MQQFDXGT_lV_PLmj-8kkvF0CX8uC60"


def test_login_cookie_round_trips_and_rejects_garbage():
    attempt = LoginAttempt.new()
    assert LoginAttempt.from_cookie(attempt.cookie()) == attempt
    assert LoginAttempt.from_cookie("only.two") is None
    assert LoginAttempt.from_cookie(None) is None


def test_the_code_is_exchanged_with_the_verifier():
    provider = FakeProvider()
    client = OidcClient(CONFIG, provider.http())
    attempt = LoginAttempt.new()
    code = provider.authorize(client.authorization_url(attempt))
    assert client.identify(code, attempt, datetime.now(UTC)).email == "anna@example.com"


def test_a_code_with_another_attempts_verifier_is_refused():
    provider = FakeProvider()
    client = OidcClient(CONFIG, provider.http())
    attempt = LoginAttempt.new()
    code = provider.authorize(client.authorization_url(attempt))
    with pytest.raises(OidcError, match="did not exchange"):
        client.identify(code, replace(attempt, verifier="x" * 43), datetime.now(UTC))


def test_discovery_is_read_once():
    provider = FakeProvider()
    client = OidcClient(CONFIG, provider.http())
    client.authorization_url(LoginAttempt.new())
    client.authorization_url(LoginAttempt.new())
    assert provider.discovery_requests == 1


def test_a_provider_that_calls_itself_something_else_is_refused():
    provider = FakeProvider()
    provider.metadata["issuer"] = "https://other.example"
    with pytest.raises(OidcError, match="calls itself"):
        OidcClient(CONFIG, provider.http()).metadata()


def test_plain_http_is_refused_except_on_loopback():
    with pytest.raises(OidcError, match="https"):
        OidcClient(
            replace(CONFIG, issuer="http://idp.example.test"), FakeProvider().http()
        ).metadata()
    local = replace(CONFIG, issuer="http://127.0.0.1:8080")
    assert OidcClient(local, FakeProvider(local).http()).metadata()["issuer"] == local.issuer


@pytest.fixture
def session(session_factory):
    with session_factory.begin() as active:
        yield active


def provider_sign_in(session, subject="subject-1", email="anna@example.com", verified=True):
    return sign_in_with_provider(session, ISSUER, subject, email, verified)


def test_nobody_gets_in_whom_an_admin_has_not_added(session):
    with pytest.raises(SignInRefused) as refused:
        provider_sign_in(session)
    assert refused.value.reason == "unknown_user"


def test_first_sign_in_links_the_account_with_the_role_given_beforehand(session):
    user = create_user(session, "anna@example.com", "Anna", Role.REVIEWER)
    actor = provider_sign_in(session)
    assert (actor.user_id, actor.role, user.oidc_subject) == (user.id, Role.REVIEWER, "subject-1")


def test_later_sign_ins_find_the_account_even_with_another_email(session):
    user = create_user(session, "anna@example.com", "Anna", Role.VIEWER)
    provider_sign_in(session)
    assert provider_sign_in(session, email="anna.new@example.com").user_id == user.id


def test_an_unverified_email_is_not_enough(session):
    create_user(session, "anna@example.com", "Anna", Role.ADMIN)
    with pytest.raises(SignInRefused) as refused:
        provider_sign_in(session, verified=False)
    assert refused.value.reason == "unverified_email"


def test_unverified_email_is_accepted_when_the_installation_says_so(session):
    create_user(session, "anna@example.com", "Anna", Role.VIEWER)
    actor = sign_in_with_provider(
        session, ISSUER, "subject-1", "anna@example.com", False, require_verified_email=False
    )
    assert actor.email == "anna@example.com"


def test_another_provider_account_cannot_take_over_a_linked_user(session):
    create_user(session, "anna@example.com", "Anna", Role.ADMIN)
    provider_sign_in(session)
    with pytest.raises(SignInRefused) as refused:
        provider_sign_in(session, subject="subject-2")
    assert refused.value.reason == "other_account"


def test_a_disabled_user_is_refused(session):
    user = create_user(session, "anna@example.com", "Anna", Role.REVIEWER)
    provider_sign_in(session)
    user.disabled = True
    with pytest.raises(SignInRefused) as refused:
        provider_sign_in(session)
    assert refused.value.reason == "disabled"


def test_an_issuer_with_a_path_is_discovered_under_it():
    """Entra ID's issuer ends in /v2.0; discovery lives below it."""
    config = replace(CONFIG, issuer="https://login.example.test/tenant-1/v2.0")
    provider = FakeProvider(config)
    client = OidcClient(config, provider.http())
    attempt = LoginAttempt.new()
    code = provider.authorize(client.authorization_url(attempt))
    assert client.identify(code, attempt, datetime.now(UTC)).subject == "subject-1"
