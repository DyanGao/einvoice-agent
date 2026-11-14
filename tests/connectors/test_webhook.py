"""The signed webhook, against a test receiver that checks every signature."""

import json
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from app.connectors import Booking, DeliveryFailed, WebhookConnector
from app.connectors.webhook import sign, verify_signature

SECRET = "s" * 40
NOW = 1_800_000_000
BOOKING = Booking(
    supplier_id="S001",
    number="RE-2026-00042",
    issue_date="2026-03-01",
    due_date=None,
    currency="EUR",
    net_total="100.00",
    vat_total="19.00",
    gross_total="119.00",
    amount_due="119.00",
    po_number=None,
    iban=None,
    reference="doc-1",
)


class Receiver:
    """Answers with the given statuses in turn and keeps what it was sent;
    refuses anything whose signature does not verify."""

    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses) or [201]
        self.received: list[tuple[dict, bytes]] = []
        app = FastAPI()

        @app.post("/hooks/invoices")
        async def hook(request: Request) -> Response:
            body = await request.body()
            if not verify_signature(
                SECRET,
                request.headers["X-Einvoice-Timestamp"],
                body,
                request.headers["X-Einvoice-Signature"],
                now=NOW,
            ):
                return Response(status_code=401)
            self.received.append((dict(request.headers), body))
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return Response(json.dumps({"id": f"wh-{len(self.received)}"}), status_code=status)

        self.client = TestClient(app, base_url="http://localhost")


def connector(receiver: Receiver, secret: str = SECRET) -> WebhookConnector:
    return WebhookConnector(
        "http://localhost/hooks/invoices", secret, client=receiver.client, clock=lambda: NOW
    )


def test_receiver_gets_the_booking_signed_and_keyed():
    receiver = Receiver()
    ref = connector(receiver).deliver(BOOKING, "einvoice:doc-1:webhook")
    ((headers, body),) = receiver.received
    assert (ref, json.loads(body), headers["idempotency-key"]) == (
        "wh-1",
        {"type": "invoice.booking", "id": "einvoice:doc-1:webhook", "booking": BOOKING.as_json()},
        "einvoice:doc-1:webhook",
    )


def test_a_retry_sends_the_same_body_under_the_same_key():
    receiver = Receiver()
    webhook = connector(receiver)
    webhook.deliver(BOOKING, "k")
    webhook.deliver(BOOKING, "k")
    (first_headers, first), (second_headers, second) = receiver.received
    assert (first, first_headers["idempotency-key"]) == (second, second_headers["idempotency-key"])


def test_request_signed_with_another_secret_is_refused_by_the_receiver():
    with pytest.raises(DeliveryFailed) as refused:
        connector(Receiver(), secret="x" * 40).deliver(BOOKING, "k")
    assert (refused.value.retryable, "401" in str(refused.value)) == (False, True)


@pytest.mark.parametrize(
    ("change", "verified"),
    [
        ({}, True),
        ({"body": b'{"tampered": true}'}, False),
        ({"secret": "y" * 40}, False),
        ({"timestamp": str(NOW - 301)}, False),  # replayed after the tolerance
        ({"timestamp": str(NOW + 301)}, False),  # from the future
        ({"signature": "md5=abc"}, False),
        ({"timestamp": "yesterday"}, False),
    ],
    ids=["intact", "body-changed", "wrong-secret", "too-old", "future", "wrong-scheme", "garbage"],
)
def test_signature_verification(change, verified):
    body = b'{"type": "invoice.booking"}'
    timestamp = str(NOW)
    signature = sign(SECRET, timestamp, body)
    args = {"secret": SECRET, "timestamp": timestamp, "body": body, "signature": signature} | change
    assert verify_signature(**args, now=NOW) is verified


@pytest.mark.parametrize(
    ("status", "retryable"), [(500, True), (503, True), (429, True), (400, False), (422, False)]
)
def test_server_errors_are_retried_and_refusals_are_not(status, retryable):
    with pytest.raises(DeliveryFailed) as failed:
        connector(Receiver(status)).deliver(BOOKING, "k")
    assert failed.value.retryable is retryable


def test_unreachable_receiver_is_retried():
    def down(request):
        raise httpx.ConnectError("connection refused")

    webhook = WebhookConnector(
        "http://localhost/hooks/invoices",
        SECRET,
        client=httpx.Client(transport=httpx.MockTransport(down)),
    )
    with pytest.raises(DeliveryFailed) as failed:
        webhook.deliver(BOOKING, "k")
    assert failed.value.retryable is True


def test_redirect_is_not_followed():
    """A signed invoice goes to the configured receiver or nowhere."""
    calls = []

    def moved(request):
        calls.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://elsewhere.example/hook"})

    webhook = WebhookConnector(
        "http://localhost/hooks/invoices",
        SECRET,
        client=httpx.Client(transport=httpx.MockTransport(moved)),
    )
    with pytest.raises(DeliveryFailed) as failed:
        webhook.deliver(BOOKING, "k")
    assert (failed.value.retryable, calls) == (False, ["http://localhost/hooks/invoices"])


@pytest.mark.parametrize(
    "url",
    ["http://receiver.example/hook", "ftp://localhost/hook", "localhost/hook"],
)
def test_receiver_must_be_https_unless_it_is_this_machine(url):
    with pytest.raises(ValueError, match="https"):
        WebhookConnector(url, SECRET)


@pytest.mark.parametrize("url", ["https://receiver.example/hook", "http://127.0.0.1:9000/hook"])
def test_https_and_local_receivers_are_accepted(url):
    assert WebhookConnector(url, SECRET).name == "webhook"


def test_short_secret_is_refused():
    with pytest.raises(ValueError, match="32"):
        WebhookConnector("https://receiver.example/hook", "short")


def test_receiver_without_an_id_in_its_answer_is_still_a_delivery():
    def accepted(request):
        return httpx.Response(202)

    webhook = WebhookConnector(
        "http://localhost/hooks/invoices",
        SECRET,
        client=httpx.Client(transport=httpx.MockTransport(accepted)),
    )
    assert webhook.deliver(replace(BOOKING, reference="doc-2"), "k") == "HTTP 202"
