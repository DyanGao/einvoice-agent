"""Encryption at rest (v1.0 Stage 6, O6): envelope encryption, one data key
per tenant.

- The **master key** (`MASTER_KEY` in `.env`, 32 random bytes, base64) never
  touches the database. It only wraps the data keys.
- Each tenant has a **data key** (`tenant_keys`): 32 random bytes, stored
  wrapped by the master key with AES-256-GCM. A wrapped key is useless
  without the master key, so the table is the installation's, not under
  row-level security. Versioned, so a tenant's key can be replaced later
  without touching the others.
- A **sealed value** is `EIA1 | key id (16 bytes) | nonce (12) | AES-GCM
  ciphertext and tag`, with the place it is stored in ("documents.content")
  as associated data: a value copied into another column does not open.
  The key id inside it says which data key opens it, so reading needs no
  context; sealing uses the key of the tenant the row belongs to.

A value without the `EIA1` mark is one stored before it was sealed: it is
read as it is, and `einvoice seal` reports and seals it.

`ENCRYPTION=off` stores everything as it comes (development only, like
`AUTH=off`); otherwise every process that reads or writes documents needs the
master key and refuses to start without it.
"""

import base64
import binascii
import hashlib
import os
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

MAGIC = b"EIA1"
_KEY_ID_BYTES = 16
_NONCE_BYTES = 12
KEY_BYTES = 32
HEADER = len(MAGIC) + _KEY_ID_BYTES + _NONCE_BYTES


class CryptoError(Exception):
    """A key is missing, wrong, or a sealed value does not open."""


class SealError(CryptoError):
    pass


def new_master_key() -> str:
    """A fresh master key as `MASTER_KEY` takes it."""
    return base64.b64encode(os.urandom(KEY_BYTES)).decode()


@dataclass(frozen=True)
class MasterKey:
    key: bytes

    @classmethod
    def parse(cls, text: str) -> "MasterKey":
        try:
            key = base64.b64decode(text.strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("MASTER_KEY must be base64 (einvoice key-generate makes one)") from exc
        if len(key) != KEY_BYTES:
            raise ValueError(f"MASTER_KEY must be {KEY_BYTES} bytes, base64; it is {len(key)}")
        return cls(key)

    @property
    def id(self) -> str:
        """Which master key wrapped a data key; says nothing about the key."""
        return hashlib.sha256(b"einvoice master key id:" + self.key).hexdigest()[:16]

    def __repr__(self) -> str:  # never the key itself, in a log or a traceback
        return f"MasterKey(id={self.id})"

    def _aad(self, tenant_id: str, version: int) -> bytes:
        return f"tenant-key:{tenant_id}:{version}".encode()

    def wrap(self, data_key: bytes, tenant_id: str, version: int) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return nonce + AESGCM(self.key).encrypt(nonce, data_key, self._aad(tenant_id, version))

    def unwrap(self, wrapped: bytes, tenant_id: str, version: int) -> bytes:
        try:
            return AESGCM(self.key).decrypt(
                wrapped[:_NONCE_BYTES], wrapped[_NONCE_BYTES:], self._aad(tenant_id, version)
            )
        except InvalidTag as exc:
            raise CryptoError(
                f"the data key of tenant {tenant_id!r} (version {version}) does not open with "
                "this MASTER_KEY"
            ) from exc


@dataclass(frozen=True)
class StoredKey:
    """A data key as stored: wrapped."""

    id: str
    tenant_id: str
    version: int
    wrapped: bytes
    master_id: str


class KeyStore(Protocol):
    def current(self, tenant_id: str) -> StoredKey | None: ...
    def by_id(self, key_id: str) -> StoredKey | None: ...
    def add(self, key: StoredKey) -> StoredKey:
        """Store it; if that tenant and version exist already (another process
        was first), the stored one."""
        ...

    def all(self) -> list[StoredKey]: ...
    def update(self, key: StoredKey) -> None:
        """Store the key's new wrapping (after a master key rotation)."""
        ...


class MemoryKeyStore:
    """Keys that live only as long as the process: tests, evals, the demo."""

    def __init__(self) -> None:
        self._keys: dict[str, StoredKey] = {}

    def current(self, tenant_id: str) -> StoredKey | None:
        mine = [key for key in self._keys.values() if key.tenant_id == tenant_id]
        return max(mine, key=lambda key: key.version, default=None)

    def by_id(self, key_id: str) -> StoredKey | None:
        return self._keys.get(key_id)

    def add(self, key: StoredKey) -> StoredKey:
        same = next(
            (
                k
                for k in self._keys.values()
                if (k.tenant_id, k.version) == (key.tenant_id, key.version)
            ),
            None,
        )
        if same is not None:
            return same
        self._keys[key.id] = key
        return key

    def all(self) -> list[StoredKey]:
        return list(self._keys.values())

    def update(self, key: StoredKey) -> None:
        self._keys[key.id] = key


class DatabaseKeyStore:
    """The `tenant_keys` table, read and written on connections of its own,
    outside whatever transaction needs a key: a key, once made, stays."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def _row(self, where) -> StoredKey | None:
        from app.db.tables import TenantKeyRow

        with self._engine.connect() as connection:
            row = connection.execute(
                select(TenantKeyRow.__table__).where(where).order_by(TenantKeyRow.version.desc())
            ).first()
        if row is None:
            return None
        return StoredKey(row.id, row.tenant_id, row.version, row.wrapped, row.master_id)

    def current(self, tenant_id: str) -> StoredKey | None:
        from app.db.tables import TenantKeyRow

        return self._row(TenantKeyRow.tenant_id == tenant_id)

    def by_id(self, key_id: str) -> StoredKey | None:
        from app.db.tables import TenantKeyRow

        return self._row(TenantKeyRow.id == key_id)

    def add(self, key: StoredKey) -> StoredKey:
        from app.db.tables import TenantKeyRow

        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(TenantKeyRow.__table__).values(
                        id=key.id,
                        tenant_id=key.tenant_id,
                        version=key.version,
                        wrapped=key.wrapped,
                        master_id=key.master_id,
                        created_at=datetime.now(UTC),
                    )
                )
        except IntegrityError:
            stored = self.current(key.tenant_id)
            if stored is None or stored.version != key.version:
                raise
            return stored
        return key

    def all(self) -> list[StoredKey]:
        from app.db.tables import TenantKeyRow

        with self._engine.connect() as connection:
            rows = connection.execute(select(TenantKeyRow.__table__)).all()
        return [StoredKey(r.id, r.tenant_id, r.version, r.wrapped, r.master_id) for r in rows]

    def update(self, key: StoredKey) -> None:
        from app.db.tables import TenantKeyRow

        table = TenantKeyRow.__table__
        with self._engine.begin() as connection:
            connection.execute(
                update(table)
                .where(table.c.id == key.id)
                .values(wrapped=key.wrapped, master_id=key.master_id)
            )

    def count(self) -> int:
        from app.db.tables import TenantKeyRow

        with self._engine.connect() as connection:
            return connection.scalar(select(func.count()).select_from(TenantKeyRow.__table__))


class Keyring:
    """Seals and opens values with the tenants' data keys, unwrapped on first
    use and kept in memory.

    `previous` are master keys being rotated away from: data keys they wrapped
    still open, and `einvoice key-rotate-master` wraps them anew under
    `master`. A tenant's current data key is looked up again after
    `CURRENT_FOR`, so a rotated key is taken up without a restart."""

    CURRENT_FOR = 60.0  # seconds

    def __init__(
        self,
        master: MasterKey,
        store: KeyStore,
        previous: tuple[MasterKey, ...] = (),
        clock=time.monotonic,
    ) -> None:
        self._master = master
        self._masters = {key.id: key for key in (*previous, master)}
        self._store = store
        self._clock = clock
        self._open: dict[str, AESGCM] = {}
        self._current: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    @property
    def master_id(self) -> str:
        return self._master.id

    @property
    def store(self) -> KeyStore:
        return self._store

    def _master_of(self, stored: StoredKey) -> MasterKey:
        master = self._masters.get(stored.master_id)
        if master is None:
            raise CryptoError(
                f"the data key of tenant {stored.tenant_id!r} was wrapped by another master key "
                f"({stored.master_id}, this one is {self._master.id}; a key being rotated away "
                "from goes into MASTER_KEY_PREVIOUS)"
            )
        return master

    def _unwrap(self, stored: StoredKey) -> AESGCM:
        data_key = self._master_of(stored).unwrap(stored.wrapped, stored.tenant_id, stored.version)
        cipher = AESGCM(data_key)
        self._open[stored.id] = cipher
        return cipher

    def _new_key(self, tenant_id: str, version: int) -> StoredKey:
        return self._store.add(
            StoredKey(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                version=version,
                wrapped=self._master.wrap(os.urandom(KEY_BYTES), tenant_id, version),
                master_id=self._master.id,
            )
        )

    def key_for(self, tenant_id: str) -> str:
        """The id of the tenant's current data key, made on first need."""
        with self._lock:
            cached = self._current.get(tenant_id)
            if cached is not None and self._clock() - cached[1] < self.CURRENT_FOR:
                return cached[0]
            stored = self._store.current(tenant_id) or self._new_key(tenant_id, 1)
            if stored.id not in self._open:
                self._unwrap(stored)
            self._current[tenant_id] = (stored.id, self._clock())
            return stored.id

    def rotate(self, tenant_id: str) -> StoredKey:
        """A new data key for the tenant, from now on its current one. The
        older ones stay: what they sealed still opens."""
        with self._lock:
            stored = self._store.current(tenant_id)
            new = self._new_key(tenant_id, 1 if stored is None else stored.version + 1)
            self._unwrap(new)
            self._current[tenant_id] = (new.id, self._clock())
            return new

    def rewrap(self, stored: StoredKey) -> StoredKey:
        """The data key wrapped by the current master key."""
        data_key = self._master_of(stored).unwrap(stored.wrapped, stored.tenant_id, stored.version)
        return replace(
            stored,
            wrapped=self._master.wrap(data_key, stored.tenant_id, stored.version),
            master_id=self._master.id,
        )

    def _cipher(self, key_id: str) -> AESGCM:
        with self._lock:
            cipher = self._open.get(key_id)
            if cipher is not None:
                return cipher
            stored = self._store.by_id(key_id)
            if stored is None:
                raise CryptoError(f"no data key {key_id} in this installation")
            return self._unwrap(stored)

    def seal(self, tenant_id: str, value: bytes, place: str) -> bytes:
        key_id = self.key_for(tenant_id)
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._cipher(key_id).encrypt(nonce, value, place.encode())
        return MAGIC + bytes.fromhex(key_id) + nonce + sealed

    def open(self, value: bytes, place: str) -> bytes:
        if not is_sealed(value):
            return value
        key_id = key_id_of(value)
        nonce = value[len(MAGIC) + _KEY_ID_BYTES : HEADER]
        try:
            return self._cipher(key_id).decrypt(nonce, value[HEADER:], place.encode())
        except InvalidTag as exc:
            raise SealError(f"a sealed value in {place} does not open: changed, or moved") from exc


def key_id_of(value: bytes) -> str:
    """The id of the data key a sealed value was sealed with."""
    return value[len(MAGIC) : len(MAGIC) + _KEY_ID_BYTES].hex()


def is_sealed(value: bytes | None) -> bool:
    return value is not None and value[: len(MAGIC)] == MAGIC


# --- the process's keyring ------------------------------------------------

_OFF = object()
_keyring: Keyring | object | None = None


def configure(keyring: Keyring | None) -> None:
    """Set the keyring every sealed column uses; None = encryption off."""
    global _keyring
    _keyring = _OFF if keyring is None else keyring


def ephemeral() -> Keyring:
    """A keyring with a random master key and keys kept in memory: for tests,
    evals and the demo, whose databases do not outlive the process."""
    keyring = Keyring(MasterKey(os.urandom(KEY_BYTES)), MemoryKeyStore())
    configure(keyring)
    return keyring


def current() -> Keyring | None:
    """The configured keyring; None when encryption is off. A process that
    forgot to configure it fails here instead of storing anything plain."""
    if _keyring is None:
        raise CryptoError(
            "encryption is not configured in this process (app.crypto.configure_from_env)"
        )
    return None if _keyring is _OFF else _keyring


def configure_from_env(engine: Engine) -> Keyring | None:
    """At start: the keyring from MASTER_KEY and the database's keys, or
    encryption off with ENCRYPTION=off. Raises ValueError without a key."""
    mode = os.environ.get("ENCRYPTION", "on").strip().lower()
    if mode not in ("on", "off"):
        raise ValueError(f"ENCRYPTION must be on or off, not {mode!r}")
    if mode == "off":
        configure(None)
        return None
    text = os.environ.get("MASTER_KEY", "")
    if not text.strip():
        raise ValueError(
            "MASTER_KEY is not set: stored invoices are encrypted with it. Make one with "
            "`einvoice key-generate`, keep it outside the database and its backups, and put it "
            "into .env (or set ENCRYPTION=off for development only)."
        )
    previous = tuple(
        MasterKey.parse(part)
        for part in os.environ.get("MASTER_KEY_PREVIOUS", "").split(",")
        if part.strip()
    )
    keyring = Keyring(MasterKey.parse(text), DatabaseKeyStore(engine), previous)
    configure(keyring)
    return keyring


def seal(tenant_id: str, value: bytes, place: str) -> bytes:
    keyring = current()
    return value if keyring is None else keyring.seal(tenant_id, value, place)


def open_sealed(value: bytes, place: str) -> bytes:
    if not is_sealed(value):
        return value
    keyring = current()
    if keyring is None:
        raise CryptoError(f"a sealed value in {place}, and encryption is off: set MASTER_KEY")
    return keyring.open(value, place)
