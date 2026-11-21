"""Sealed columns (v1.0 Stage 6, increment 2): invoice data stored encrypted
with the tenant's data key (app/crypto.py).

`Sealed(place)` is a column type: what the application puts in (a JSON
value, or text) is stored as a sealed value, and comes back as it went in.
A value stored before it was sealed (JSON or text as bytes) is read as it is,
and `einvoice seal` seals it.

Which tenant's key seals a value: the one the session acts for. `act_for`
(app/db/rls.py) records it on the session; right before the session writes
(a flush, or a statement it executes) it is handed to the column type on the
same thread, through a context variable that each write sets afresh. A
session that acts for nobody (tests, the evals, which have one tenant)
seals with the default tenant's key; on Postgres such a session cannot write
a tenant's rows at all (row-level security). Reading needs no tenant: the
sealed value names its key.

A sealed column cannot be searched or sorted by in SQL; the few columns that
are (ERP ids, states, dates) stay plain.
"""

import json
from contextvars import ContextVar
from typing import Any

from sqlalchemy import LargeBinary, event
from sqlalchemy.orm import Session
from sqlalchemy.types import TypeDecorator

from app import crypto

TENANT_INFO = "einvoice_tenant_id"
DEFAULT_TENANT_ID = "default"

_sealing_for: ContextVar[str | None] = ContextVar("einvoice_sealing_for", default=None)


def sealing_tenant() -> str:
    return _sealing_for.get() or DEFAULT_TENANT_ID


@event.listens_for(Session, "before_flush")
def _before_flush(session: Session, flush_context, instances) -> None:
    _sealing_for.set(session.info.get(TENANT_INFO))


@event.listens_for(Session, "do_orm_execute")
def _before_execute(state) -> None:
    _sealing_for.set(state.session.info.get(TENANT_INFO))


class Sealed(TypeDecorator):
    """A JSON value (`kind="json"`) or text (`kind="text"`), stored sealed."""

    impl = LargeBinary
    cache_ok = True

    def __init__(self, place: str, kind: str = "json") -> None:
        if kind not in ("json", "text"):
            raise ValueError(f"kind is json or text, not {kind!r}")
        super().__init__()
        self.place = place
        self.kind = kind

    def process_bind_param(self, value: Any, dialect) -> bytes | None:
        if value is None:
            return None
        plain = (
            json.dumps(value, ensure_ascii=False).encode()
            if self.kind == "json"
            else str(value).encode()
        )
        return crypto.seal(sealing_tenant(), plain, self.place)

    def process_result_value(self, value: bytes | None, dialect) -> Any:
        if value is None:
            return None
        plain = crypto.open_sealed(bytes(value), self.place)
        return json.loads(plain) if self.kind == "json" else plain.decode()

    def open_raw(self, value: bytes) -> Any:
        """A stored value as the application gets it (for `einvoice seal`)."""
        return self.process_result_value(value, None)

    def plain_bytes(self, value: Any) -> bytes:
        """The value as stored before sealing (for `einvoice seal --open`)."""
        if self.kind == "json":
            return json.dumps(value, ensure_ascii=False).encode()
        return str(value).encode()
