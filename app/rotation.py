"""Key rotation (v1.0 Stage 6, increment 3).

A new master key: nothing stored is sealed again, only the data keys are
wrapped anew.

    1. einvoice key-generate                     a new key
    2. .env: MASTER_KEY=<new>, MASTER_KEY_PREVIOUS=<old>; restart the services
       (both keys open the data keys now)
    3. einvoice key-rotate-master --apply        every data key wrapped by the new one
    4. .env: remove MASTER_KEY_PREVIOUS; restart

Keep the old master key as long as a backup made before step 3 may be
restored: its data keys are wrapped by the old one (MASTER_KEY_PREVIOUS opens
them again).

A tenant's new data key: what it sealed is sealed again with the new one.

    einvoice key-rotate-tenant SLUG [--apply]

The services take the new key up within a minute (app/crypto.py,
Keyring.CURRENT_FOR); `einvoice seal --apply` afterwards seals again what they
wrote with the old one meanwhile. Older versions of the key are kept: deleting
one makes everything still sealed with it unreadable, which is what the
retention increment will decide on, not rotation.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import crypto
from app.db.tables import TenantRow
from app.sealing import Report, seal_documents


@dataclass(frozen=True)
class MasterRotation:
    rewrapped: int
    already: int


def _keyring() -> crypto.Keyring:
    keyring = crypto.current()
    if keyring is None:
        raise crypto.CryptoError("encryption is off (ENCRYPTION=off): there is no key to rotate")
    return keyring


def rotate_master(*, apply: bool) -> MasterRotation:
    """Wrap every data key not wrapped by the current master key anew (it must
    open with MASTER_KEY_PREVIOUS). Without `apply` only counted."""
    keyring = _keyring()
    rewrapped = already = 0
    for stored in keyring.store.all():
        if stored.master_id == keyring.master_id:
            already += 1
            continue
        new = keyring.rewrap(stored)  # also proves the previous key opens it
        rewrapped += 1
        if apply:
            keyring.store.update(new)
    return MasterRotation(rewrapped, already)


@dataclass(frozen=True)
class TenantRotation:
    version: int | None  # the new key's; None without `apply`
    report: Report


def rotate_tenant(
    session_factory: sessionmaker[Session], slug: str, *, apply: bool
) -> TenantRotation:
    """A new data key for the tenant, and everything of it sealed again with
    it. Without `apply`: what would be sealed again."""
    keyring = _keyring()
    with session_factory() as session:
        tenant_id = session.scalar(select(TenantRow.id).where(TenantRow.slug == slug))
    if tenant_id is None:
        raise ValueError(f"no tenant {slug!r}")
    if not apply:
        # All its values would be sealed again: count them as they are now.
        report = seal_documents(session_factory, apply=False, tenant_ids=[tenant_id])
        report.changed[slug] += report.already[slug]
        report.already[slug] = 0
        return TenantRotation(None, report)
    new = keyring.rotate(tenant_id)
    return TenantRotation(
        new.version, seal_documents(session_factory, apply=True, tenant_ids=[tenant_id])
    )
