"""Who a request comes from (v1.0 Stage 1).

People sign in through the identity provider (increment 2) or, for the
built-in admin account used for first setup, with a password. Scripts and
integrations use API tokens. Either way the request acts as a user with one
role: viewer (reads), reviewer (corrects, approves, rejects, reopens, uploads)
or admin (everything, and accounts).

Nothing that could be used to sign in is stored as it is: passwords as salted
scrypt hashes, tokens and session ids as SHA-256 (they are long and random, so
a fast hash is enough). A token or session id is shown once, when made.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.tables import ApiTokenRow, SessionRow, UserRow

TOKEN_PREFIX = "eia_"  # noqa: S105 -- a marker, not a secret
SESSION_LIFETIME = timedelta(hours=8)
# scrypt cost: n=2**14, r=8, p=1 (OWASP's minimum for scrypt), 16-byte salt.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}
MIN_PASSWORD_LENGTH = 12


class Role(StrEnum):
    VIEWER = "viewer"
    REVIEWER = "reviewer"
    ADMIN = "admin"

    def allows(self, needed: "Role") -> bool:
        order = (Role.VIEWER, Role.REVIEWER, Role.ADMIN)
        return order.index(self) >= order.index(needed)


@dataclass(frozen=True)
class Actor:
    """Who a request acts as. `verified` is False only when sign-in is off and
    the name was typed into the UI."""

    name: str
    role: Role
    user_id: str | None = None
    email: str | None = None
    verified: bool = True


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    candidate = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), **_SCRYPT)
    return hmac.compare_digest(candidate.hex(), digest)


def _actor(user: UserRow) -> Actor:
    return Actor(name=user.display_name, role=Role(user.role), user_id=user.id, email=user.email)


def create_user(
    session: Session, email: str, display_name: str, role: Role, password: str | None = None
) -> UserRow:
    email = email.strip().lower()
    if session.scalar(select(UserRow).where(func.lower(UserRow.email) == email)) is not None:
        raise ValueError(f"a user with the email {email} already exists")
    if password is not None and len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"a password needs at least {MIN_PASSWORD_LENGTH} characters")
    user = UserRow(
        email=email,
        display_name=display_name.strip(),
        role=Role(role).value,
        password_hash=None if password is None else hash_password(password),
    )
    session.add(user)
    session.flush()
    return user


def find_user(session: Session, email: str) -> UserRow | None:
    return session.scalar(select(UserRow).where(UserRow.email == email.strip().lower()))


def sign_in(session: Session, email: str, password: str) -> Actor | None:
    """The built-in account's password sign-in. The same answer, and about the
    same time, whether the email is unknown or the password wrong."""
    user = find_user(session, email)
    if user is None or user.password_hash is None:
        verify_password(password, hash_password("timing"))
        return None
    if not verify_password(password, user.password_hash) or user.disabled:
        return None
    return _actor(user)


def create_token(session: Session, user_id: str, name: str) -> str:
    plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
    session.add(ApiTokenRow(user_id=user_id, name=name, token_hash=_sha256(plain)))
    session.flush()
    return plain


def revoke_token(session: Session, plain: str, now: datetime | None = None) -> bool:
    row = session.scalar(select(ApiTokenRow).where(ApiTokenRow.token_hash == _sha256(plain)))
    if row is None:
        return False
    row.revoked_at = now or datetime.now(UTC)
    session.flush()
    return True


def authenticate_token(session: Session, plain: str, now: datetime) -> Actor | None:
    row = session.scalar(select(ApiTokenRow).where(ApiTokenRow.token_hash == _sha256(plain)))
    if row is None or row.revoked_at is not None:
        return None
    user = session.get(UserRow, row.user_id)
    if user is None or user.disabled:
        return None
    row.last_used_at = now
    return _actor(user)


def start_session(
    session: Session, user_id: str, now: datetime, lifetime: timedelta = SESSION_LIFETIME
) -> str:
    session_id = secrets.token_urlsafe(32)
    session.add(SessionRow(id_hash=_sha256(session_id), user_id=user_id, expires_at=now + lifetime))
    session.flush()
    return session_id


def _aware(moment: datetime) -> datetime:
    # SQLite returns naive datetimes; they were stored in UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def authenticate_session(session: Session, session_id: str, now: datetime) -> Actor | None:
    row = session.get(SessionRow, _sha256(session_id))
    if row is None or _aware(row.expires_at) <= now:
        return None
    user = session.get(UserRow, row.user_id)
    if user is None or user.disabled:
        return None
    return _actor(user)


def end_session(session: Session, session_id: str) -> None:
    row = session.get(SessionRow, _sha256(session_id))
    if row is not None:
        session.delete(row)
        session.flush()
