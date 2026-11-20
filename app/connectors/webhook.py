"""Posts each decided invoice to a receiver the installation names, signed.

The request is `{"type": "invoice.booking", "id": <key>, "booking": {...}}`
with these headers:

- `Idempotency-Key`: the same on every attempt, so the receiver can drop a
  repeat (a retry after an answer was lost);
- `X-Einvoice-Timestamp`: when it was signed, in Unix seconds;
- `X-Einvoice-Signature`: `sha256=` and the hex HMAC-SHA256 of
  `<timestamp>.<body>` under the shared secret.

A receiver checks the signature with `verify_signature` (the reference
implementation) before trusting anything in the body: it proves the request
came from someone holding the secret and was not changed on the way, and
the timestamp lets it refuse an old request replayed later.

Server errors, 429 and no answer are retried by the outbox; any other
refusal is not. Redirects are not followed: a signed invoice goes to the
configured receiver or nowhere.
"""

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from app.connectors.base import Booking, DeliveryFailed
from app.texts import Text

SIGNATURE_HEADER = "X-Einvoice-Signature"
TIMESTAMP_HEADER = "X-Einvoice-Timestamp"
# How far a receiver accepts a timestamp from its own clock, either way.
TOLERANCE_SECONDS = 300
MIN_SECRET_LENGTH = 32
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def sign(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"sha256={digest.hexdigest()}"


def verify_signature(
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    *,
    now: float | None = None,
    tolerance: int = TOLERANCE_SECONDS,
) -> bool:
    """Whether a received request is genuine and fresh."""
    try:
        signed_at = int(timestamp)
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - signed_at) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    local = parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS
    if parts.scheme != "https" and not local:
        raise ValueError(
            f"webhook URL {url!r} must use https: the invoice and its bank account travel in it "
            "(plain http only to this machine)"
        )


class WebhookConnector:
    name = "webhook"

    def __init__(
        self,
        url: str,
        secret: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        _check_url(url)
        if len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(f"webhook secret must be at least {MIN_SECRET_LENGTH} characters")
        self._url = url
        self._secret = secret
        self._client = client or httpx.Client(timeout=timeout)
        self._clock = clock

    def deliver(self, booking: Booking, key: str) -> str:
        body = json.dumps(
            {"type": "invoice.booking", "id": key, "booking": booking.as_json()},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
        timestamp = str(int(self._clock()))
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": key,
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: sign(self._secret, timestamp, body),
        }
        try:
            response = self._client.post(
                self._url, content=body, headers=headers, follow_redirects=False
            )
        except httpx.HTTPError as exc:
            raise DeliveryFailed(
                Text("delivery.webhook_unreachable", detail=f"{type(exc).__name__}: {exc}"),
                retryable=True,
            ) from exc
        status = response.status_code
        if 200 <= status < 300:
            try:
                return str(response.json()["id"])
            except (ValueError, KeyError, TypeError):
                return f"HTTP {status}"
        if status >= 500 or status == 429:
            raise DeliveryFailed(Text("delivery.webhook_failed", status=status), retryable=True)
        if 300 <= status < 400:
            raise DeliveryFailed(
                Text(
                    "delivery.webhook_redirect",
                    status=status,
                    location=repr(response.headers.get("location")),
                ),
                retryable=False,
            )
        raise DeliveryFailed(Text("delivery.webhook_refused", status=status), retryable=False)
