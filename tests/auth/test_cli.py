"""Setting up accounts and tokens from the command line."""

from app import db
from app.auth import authenticate_token, sign_in
from app.cli import main
from tests.auth.test_identity import NOW

PASSWORD = "first-setup-password"


def run(session_factory, *argv, password=PASSWORD):
    return main(list(argv), session_factory=session_factory, password_prompt=lambda: password)


def test_first_admin_is_added_with_a_password_asked_for_not_typed_on_the_command_line(
    session_factory, capsys
):
    code = run(
        session_factory,
        "user-add",
        "admin@example.com",
        "--name",
        "Admin",
        "--role",
        "admin",
        "--password",
    )
    assert code == 0
    assert PASSWORD not in capsys.readouterr().out
    with session_factory() as session:
        assert sign_in(session, "admin@example.com", PASSWORD).role == "admin"


def test_short_password_is_refused(session_factory, capsys):
    code = run(
        session_factory,
        "user-add",
        "a@example.com",
        "--name",
        "A",
        "--role",
        "admin",
        "--password",
        password="short",
    )
    assert code == 1
    assert "12 characters" in capsys.readouterr().err


def test_token_is_printed_once_and_works_until_revoked(session_factory, capsys):
    run(
        session_factory,
        "user-add",
        "watch@example.com",
        "--name",
        "Folder watch",
        "--role",
        "reviewer",
    )
    capsys.readouterr()
    assert run(session_factory, "token-create", "watch@example.com", "--name", "folder watch") == 0
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert token.startswith("eia_")
    with session_factory() as session:
        assert authenticate_token(session, token, NOW).email == "watch@example.com"
    assert run(session_factory, "token-revoke", token) == 0
    with session_factory() as session:
        assert authenticate_token(session, token, NOW) is None


def test_token_for_an_unknown_user_is_refused(session_factory, capsys):
    assert run(session_factory, "token-create", "nobody@example.com", "--name", "x") == 1
    assert "no user" in capsys.readouterr().err
    with session_factory() as session:
        assert session.query(db.ApiTokenRow).count() == 0
