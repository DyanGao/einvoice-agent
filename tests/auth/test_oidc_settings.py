"""The identity provider's settings: all of them, or none."""

import pytest

from app.settings import _oidc

FULL = {
    "OIDC_ISSUER": "https://idp.example.test",
    "OIDC_CLIENT_ID": "einvoice",
    "OIDC_CLIENT_SECRET": "secret",
    "OIDC_REDIRECT_URI": "https://einvoice.example.test/auth/oidc/callback",
}


def test_no_issuer_means_no_provider():
    assert _oidc({}) is None


def test_a_full_configuration_requires_verified_emails_by_default():
    config = _oidc(FULL)
    assert (config.client_id, config.require_verified_email) == ("einvoice", True)


def test_an_issuer_without_the_rest_fails_at_start():
    with pytest.raises(ValueError, match="OIDC_CLIENT_SECRET"):
        _oidc({key: value for key, value in FULL.items() if key != "OIDC_CLIENT_SECRET"})


def test_the_email_switch_takes_only_true_or_false():
    assert _oidc(FULL | {"OIDC_REQUIRE_VERIFIED_EMAIL": "false"}).require_verified_email is False
    with pytest.raises(ValueError, match="true or false"):
        _oidc(FULL | {"OIDC_REQUIRE_VERIFIED_EMAIL": "no"})


def test_cookie_secure_takes_only_auto_or_true():
    from app.settings import _cookie_secure

    assert _cookie_secure("true") == "true"
    with pytest.raises(ValueError, match="auto or true"):
        _cookie_secure("yes")
