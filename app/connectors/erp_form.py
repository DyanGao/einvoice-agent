"""Books invoices through the ERP's HTML screens with a browser: the last
resort, for an ERP whose only way in is a clerk's form (ARCHITECTURE ADR 4).

What it knows of the screens is below: the labels of the booking form and
the booking page, and their German number and date formats. It finds every
field by its label, so moved fields do not matter and a renamed one stops it.

Safety, in the order it matters:

- The browser reaches only the allowlisted origins: `host:port`, or a bare
  `host` for its default port (80 for http, 443 for https). Another port on an
  allowed host (a database, the application's own API) is not allowed. Every
  request goes through a guard; anything else is blocked, logged and fails the
  delivery.
- A screen has no idempotency key, so the connector searches the ERP for the
  invoice's reference first. Already booked with the same values: that
  booking is the answer (also after a crash between booking and recording);
  with other values: refused.
- It fills the form, reads the fields back, and only then submits; after
  submitting it reads the stored booking back, field by field. A field it
  cannot find or a value that differs fails the delivery for a person, with
  a screenshot, rather than leaving a wrong booking unnoticed.
- Every action is logged to `<log_dir>/<key>/actions.jsonl`.
"""

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from app.connectors.base import Booking, DeliveryFailed

# (field of the Booking, label on the ERP's screens)
LABELS = (
    ("supplier_id", "Kreditor"),
    ("number", "Rechnungsnummer"),
    ("issue_date", "Rechnungsdatum"),
    ("due_date", "Fällig am"),
    ("currency", "Währung"),
    ("net_total", "Nettobetrag"),
    ("vat_total", "Steuerbetrag"),
    ("gross_total", "Bruttobetrag"),
    ("amount_due", "Zahlbetrag"),
    ("po_number", "Bestellnummer"),
    ("iban", "IBAN"),
    ("reference", "Ihre Referenz"),
)
_DATES = {"issue_date", "due_date"}
_AMOUNTS = {"net_total", "vat_total", "gross_total", "amount_due"}
_EMPTY = "—"
_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}


def _origin(host: str | None, port: int | None, scheme: str) -> tuple[str, int | None]:
    return ((host or "").lower(), port if port is not None else _DEFAULT_PORTS.get(scheme))


def parse_allowed(entry: str) -> tuple[str, int | None]:
    """`host:port`, `[v6]:port` or a bare host. A bare host stands for its
    default port, which is only known per request scheme: port None."""
    parts = urlsplit(f"//{entry.strip()}")
    return ((parts.hostname or "").lower(), parts.port)


_BOOKING_URL = re.compile(r".*/ui/bookings/(\d+)$")


def german_amount(value: str) -> str:
    whole, cents = f"{Decimal(value):.2f}".split(".")
    sign, digits = ("-", whole[1:]) if whole.startswith("-") else ("", whole)
    groups = [digits[max(0, end - 3) : end] for end in range(len(digits), 0, -3)]
    return f"{sign}{'.'.join(reversed(groups))},{cents}"


def typed(field: str, value: str | None) -> str:
    """What a clerk types into the form for this value."""
    if value is None:
        return ""
    if field in _AMOUNTS:
        return german_amount(value)
    if field in _DATES:
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    return value


def same(field: str, expected: str | None, shown: str) -> bool:
    """Whether the booking page shows this value."""
    if expected is None:
        return shown == _EMPTY
    if field in _AMOUNTS:
        try:
            return Decimal(shown.replace(".", "").replace(",", ".")) == Decimal(expected)
        except InvalidOperation:
            return False
    return shown == typed(field, expected)


class _FormChanged(Exception):
    pass


class ErpFormConnector:
    # Same identity as the API connector: whichever channel an installation
    # uses, an invoice is delivered to the ERP once.
    name = "erp"
    channel = "form"

    def __init__(
        self,
        base_url: str,
        *,
        allowed_hosts: set[str],
        log_dir: Path,
        timeout_ms: int = 10_000,
    ) -> None:
        self._allowed = {parse_allowed(entry) for entry in allowed_hosts}
        if not self.permits(base_url):
            raise ValueError(
                f"ERP address {urlsplit(base_url).netloc!r} is not on the browser allowlist "
                f"{sorted(allowed_hosts)} (entries are host:port; a bare host allows only "
                "its default port)"
            )
        self._base = base_url.rstrip("/")
        self._log_dir = Path(log_dir)
        self._timeout_ms = timeout_ms

    def permits(self, url: str) -> bool:
        """Whether the browser may request this URL."""
        parts = urlsplit(url)
        host, port = _origin(parts.hostname, parts.port, parts.scheme)
        if (host, port) in self._allowed:
            return True
        return (host, None) in self._allowed and port == _DEFAULT_PORTS.get(parts.scheme)

    # --- the browser session -------------------------------------------------

    def deliver(self, booking: Booking, key: str) -> str:
        folder = self._log_dir / re.sub(r"[^A-Za-z0-9._-]", "_", key)
        folder.mkdir(parents=True, exist_ok=True)
        attempt = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        log = (folder / "actions.jsonl").open("a", encoding="utf-8")
        blocked: list[str] = []

        def record(action: str, **detail) -> None:
            entry = {"at": datetime.now(UTC).isoformat(), "attempt": attempt, "action": action}
            log.write(json.dumps(entry | detail, ensure_ascii=False) + "\n")
            log.flush()

        def guard(route) -> None:
            url = route.request.url
            if self.permits(url):
                route.continue_()
            else:
                blocked.append(url)
                record("blocked", url=url)
                route.abort("blockedbyclient")

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    context = browser.new_context()
                    context.route("**/*", guard)
                    page = context.new_page()
                    page.set_default_timeout(self._timeout_ms)
                    try:
                        return self._book(page, booking, record)
                    except DeliveryFailed:
                        self._screenshot(page, folder, attempt, record)
                        raise
                    except _FormChanged as exc:
                        self._screenshot(page, folder, attempt, record)
                        raise DeliveryFailed(
                            f"the ERP's booking form has changed: {exc}; nothing was booked",
                            retryable=False,
                        ) from exc
                    except PlaywrightError as exc:
                        self._screenshot(page, folder, attempt, record)
                        if blocked:
                            raise DeliveryFailed(
                                f"the browser was sent to {blocked[0]}, which is not on the "
                                "allowlist; blocked, nothing was booked there",
                                retryable=False,
                            ) from exc
                        # Not reachable, too slow: safe to try again, since every
                        # attempt searches for an existing booking first.
                        raise DeliveryFailed(
                            f"ERP screens not usable: {str(exc).splitlines()[0]}", retryable=True
                        ) from exc
                finally:
                    browser.close()
        finally:
            log.close()

    def _screenshot(self, page: Page, folder: Path, attempt: str, record) -> None:
        path = folder / f"failure-{attempt}.png"
        try:
            page.screenshot(path=path, full_page=True)
            record("screenshot", path=str(path))
        except PlaywrightError as exc:
            record("screenshot_failed", error=str(exc).splitlines()[0])

    # --- the clerk's steps ---------------------------------------------------

    def _goto(self, page: Page, url: str, record) -> None:
        record("goto", url=url)
        response = page.goto(url)
        if response is not None and response.status >= 500:
            raise DeliveryFailed(f"ERP answered {response.status} for {url}", retryable=True)

    def _book(self, page: Page, booking: Booking, record) -> str:
        existing = self._find(page, booking.reference, record)
        if existing is not None:
            record("found_existing", booking=existing)
            self._read_back(
                page, existing, booking, record, refuse="already booked with other values"
            )
            return existing

        self._goto(page, f"{self._base}/ui/bookings/new", record)
        values = booking.as_json()
        for field, label in LABELS:
            box = page.get_by_label(label, exact=True)
            if box.count() != 1:
                raise _FormChanged(f"field {label!r} not found")
            text = typed(field, values[field])
            record("fill", label=label, value=text)
            box.fill(text)
        for field, label in LABELS:
            if page.get_by_label(label, exact=True).input_value() != typed(field, values[field]):
                raise _FormChanged(f"field {label!r} does not keep what was typed")
        submit = page.get_by_role("button", name="Buchen", exact=True)
        if submit.count() != 1:
            raise _FormChanged("button 'Buchen' not found")
        record("click", button="Buchen")
        submit.click()
        page.wait_for_url(_BOOKING_URL)
        booking_id = _BOOKING_URL.match(page.url).group(1)
        self._read_back(page, booking_id, booking, record, refuse="booked with values that differ")
        record("booked", booking=booking_id)
        return booking_id

    def _find(self, page: Page, reference: str, record) -> str | None:
        self._goto(page, f"{self._base}/ui/bookings?reference={quote(reference)}", record)
        table = page.get_by_role("table", name="Buchungen")
        if table.count() == 0:
            return None
        ids = []
        for row in table.locator("tbody tr").all():
            cells = row.locator("td").all_inner_texts()
            if len(cells) == 3 and cells[2] == reference:
                ids.append(cells[0].removeprefix("Buchung ").strip())
        if len(ids) > 1:
            raise DeliveryFailed(
                f"the ERP holds {len(ids)} bookings for reference {reference}: {', '.join(ids)}",
                retryable=False,
            )
        return ids[0] if ids else None

    def _read_back(
        self, page: Page, booking_id: str, booking: Booking, record, *, refuse: str
    ) -> None:
        self._goto(page, f"{self._base}/ui/bookings/{booking_id}", record)
        shown = dict(
            page.eval_on_selector(
                "dl[aria-label='Buchung']",
                "dl => [...dl.querySelectorAll('dt')].map(dt => [dt.textContent, "
                "dt.nextElementSibling ? dt.nextElementSibling.textContent : null])",
            )
        )
        values = booking.as_json()
        differ = [
            label
            for field, label in LABELS
            if label not in shown or not same(field, values[field], shown[label] or "")
        ]
        record("read_back", booking=booking_id, differ=differ)
        if differ:
            raise DeliveryFailed(
                f"booking {booking_id} in the ERP: {refuse}: {', '.join(differ)}", retryable=False
            )
