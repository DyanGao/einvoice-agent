"""Who a request comes from (v1.0 Stage 1).

People sign in through the identity provider (`app/auth/oidc.py`) or, for the
built-in admin account used for first setup, with a password. Scripts and
integrations use API tokens. Either way the request acts as a user in one
tenant, with the role the user has there (a membership): viewer (reads),
reviewer (corrects, approves, rejects, reopens, uploads) or admin
(everything, and accounts). A session works in one tenant, a token acts in
the tenant it was made for.

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

from app.db.tables import DEFAULT_TENANT_ID, ApiTokenRow, MembershipRow, SessionRow, UserRow

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
    """Who a request acts as, and for which tenant. `verified` is False only
    when sign-in is off and the name was typed into the UI."""

    name: str
    role: Role
    tenant_id: str
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


def _membership(session: Session, user_id: str, tenant_id: str) -> MembershipRow | None:
    return session.get(MembershipRow, (user_id, tenant_id))


def _actor(session: Session, user: UserRow, tenant_id: str) -> Actor | None:
    """The user acting in the tenant; None if they are not a member of it."""
    membership = _membership(session, user.id, tenant_id)
    if membership is None:
        return None
    return Actor(
        name=user.display_name,
        role=Role(membership.role),
        tenant_id=tenant_id,
        user_id=user.id,
        email=user.email,
    )


def _first_tenant(session: Session, user: UserRow) -> Actor | None:
    """Where a person starts working after signing in: the tenant they
    joined first. Switching comes with the admin UI (Stage 2, increment 5)."""
    tenant_id = session.scalar(
        select(MembershipRow.tenant_id)
        .where(MembershipRow.user_id == user.id)
        .order_by(MembershipRow.created_at, MembershipRow.tenant_id)
        .limit(1)
    )
    return None if tenant_id is None else _actor(session, user, tenant_id)


def add_membership(session: Session, user_id: str, tenant_id: str, role: Role) -> MembershipRow:
    """Give the user a role in the tenant, or change the role they have there."""
    membership = _membership(session, user_id, tenant_id)
    if membership is None:
        membership = MembershipRow(user_id=user_id, tenant_id=tenant_id, role=Role(role).value)
        session.add(membership)
    else:
        membership.role = Role(role).value
    session.flush()
    return membership


def create_user(
    session: Session,
    email: str,
    display_name: str,
    role: Role,
    password: str | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> UserRow:
    """A person with `role` in `tenant_id`."""
    email = email.strip().lower()
    if session.scalar(select(UserRow).where(func.lower(UserRow.email) == email)) is not None:
        raise ValueError(f"a user with the email {email} already exists")
    if password is not None and len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"a password needs at least {MIN_PASSWORD_LENGTH} characters")
    user = UserRow(
        email=email,
        display_name=display_name.strip(),
        password_hash=None if password is None else hash_password(password),
    )
    session.add(user)
    session.flush()
    add_membership(session, user.id, tenant_id, role)
    return user


def find_user(session: Session, email: str) -> UserRow | None:
    return session.scalar(select(UserRow).where(UserRow.email == email.strip().lower()))


def sign_in(session: Session, email: str, password: str) -> Actor | None:
    """The built-in account's password sign-in, into the first tenant the user
    joined. The same answer, and about the same time, whether the email is
    unknown or the password wrong."""
    user = find_user(session, email)
    if user is None or user.password_hash is None:
        verify_password(password, hash_password("timing"))
        return None
    if not verify_password(password, user.password_hash) or user.disabled:
        return None
    return _first_tenant(session, user)


class SignInRefused(Exception):
    """The provider knows the person, but this installation does not let them
    in. `reason` is a code for the UI: unverified_email, unknown_user,
    disabled, other_account or no_tenant."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


def sign_in_with_provider(
    session: Session,
    issuer: str,
    subject: str,
    email: str | None,
    email_verified: bool,
    require_verified_email: bool = True,
) -> Actor:
    """The user behind an identity provider's account. Nobody gets in who an
    admin has not added: the first sign-in finds the user by email (verified by
    the provider) and links the account; later ones find it by the account."""
    user = session.scalar(
        select(UserRow).where(UserRow.oidc_issuer == issuer, UserRow.oidc_subject == subject)
    )
    if user is None:
        if email is None or (require_verified_email and not email_verified):
            raise SignInRefused("unverified_email", f"no verified email for {subject} at {issuer}")
        user = find_user(session, email)
        if user is None:
            raise SignInRefused("unknown_user", f"{email} is not a user here")
        if user.oidc_subject is not None:
            raise SignInRefused(
                "other_account", f"{email} is linked to another account at the provider"
            )
        user.oidc_issuer, user.oidc_subject = issuer, subject
        session.flush()
    if user.disabled:
        raise SignInRefused("disabled", f"{user.email} is disabled")
    actor = _first_tenant(session, user)
    if actor is None:
        raise SignInRefused("no_tenant", f"{user.email} is not a member of any tenant")
    return actor


def create_token(
    session: Session, user_id: str, name: str, tenant_id: str = DEFAULT_TENANT_ID
) -> str:
    """A token acting as the user in the tenant, with the role they have there."""
    if _membership(session, user_id, tenant_id) is None:
        raise ValueError(f"the user is not a member of tenant {tenant_id}")
    plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
    session.add(
        ApiTokenRow(user_id=user_id, tenant_id=tenant_id, name=name, token_hash=_sha256(plain))
    )
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
    actor = _actor(session, user, row.tenant_id)
    if actor is not None:
        row.last_used_at = now
    return actor


def start_session(
    session: Session,
    user_id: str,
    tenant_id: str,
    now: datetime,
    lifetime: timedelta = SESSION_LIFETIME,
) -> str:
    session_id = secrets.token_urlsafe(32)
    session.add(
        SessionRow(
            id_hash=_sha256(session_id),
            user_id=user_id,
            tenant_id=tenant_id,
            expires_at=now + lifetime,
        )
    )
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
    # A membership taken away ends the session's access at once.
    return _actor(session, user, row.tenant_id)


def end_session(session: Session, session_id: str) -> None:
    row = session.get(SessionRow, _sha256(session_id))
    if row is not None:
        session.delete(row)
        session.flush()
