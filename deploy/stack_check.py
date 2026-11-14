"""Smoke test of the whole application in Docker (`make stack`, then
`make stack-check`): one sample invoice uploaded to the API is processed by
the worker, decided `auto`, booked in the mock ERP by the delivery loop; the
review UI answers. Exits non-zero, saying where it stopped, otherwise.

    uv run python deploy/stack_check.py [--api URL] [--erp URL] [--ui URL]
"""

import argparse
import sys
import time
from pathlib import Path

import httpx

SAMPLE = Path("sample_invoices/01-RE-2026-04319.xml")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--erp", default="http://127.0.0.1:8100")
    parser.add_argument("--ui", default="http://127.0.0.1:3000")
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()

    ui = httpx.get(args.ui, timeout=10)
    if ui.status_code != 200:
        print(f"the review UI answered {ui.status_code}")
        return 1
    uploaded = httpx.post(
        f"{args.api}/documents",
        files={"file": (SAMPLE.name, SAMPLE.read_bytes(), "application/xml")},
        timeout=30,
    ).json()
    document_id = uploaded["id"]
    print(f"uploaded {SAMPLE.name} as {document_id} (duplicate: {uploaded['duplicate']})")
    deadline = time.monotonic() + args.timeout
    detail: dict = {}
    while time.monotonic() < deadline:
        detail = httpx.get(f"{args.api}/documents/{document_id}", timeout=10).json()
        if any(d["status"] == "delivered" for d in detail["deliveries"]):
            break
        time.sleep(2)
    else:
        print(
            f"not booked within {args.timeout:.0f}s: status {detail.get('status')}, "
            f"review {detail.get('review_state')}, deliveries {detail.get('deliveries')}"
        )
        return 1
    bookings = httpx.get(
        f"{args.erp}/booked-invoices", params={"reference": document_id}, timeout=10
    ).json()["booked_invoices"]
    if len(bookings) != 1:
        print(f"the mock ERP holds {len(bookings)} bookings for {document_id}, not 1")
        return 1
    print(
        f"processed ({detail['status']}, {detail['decision']['route']}), booked in the mock ERP "
        f"as no. {bookings[0]['id']}; the review UI answers"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
