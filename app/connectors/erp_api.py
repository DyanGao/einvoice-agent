"""Books invoices through the ERP's API (mock_erp: POST /booked-invoices).

The ERP stores one booking per Idempotency-Key and answers a repeat with
the booking it already has, which is what makes a retry after a lost answer
safe.
"""

import httpx

from app.connectors.base import Booking, DeliveryFailed


class ErpApiConnector:
    name = "erp_api"

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def deliver(self, booking: Booking, key: str) -> str:
        try:
            response = self._client.post(
                "/booked-invoices", json=booking.as_json(), headers={"Idempotency-Key": key}
            )
        except httpx.HTTPError as exc:
            raise DeliveryFailed(
                f"ERP not reachable: {type(exc).__name__}: {exc}", retryable=True
            ) from exc
        if response.status_code in (200, 201):
            return str(response.json()["id"])
        detail = _detail(response)
        if response.status_code >= 500 or response.status_code == 429:
            raise DeliveryFailed(
                f"ERP failed with {response.status_code}: {detail}", retryable=True
            )
        raise DeliveryFailed(
            f"ERP refused the booking ({response.status_code}): {detail}", retryable=False
        )


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", ""))[:300]
    except ValueError:
        return response.text[:300]
