"""The mock ERP's HTML booking screens: what an old ERP without a booking API
offers a clerk, and what the Playwright connector drives.

German labels, German number and date formats, plain HTML forms, no
JavaScript. Every value shown is HTML-escaped: invoice content is data.
Unlike the API there is no idempotency key; whoever books through these
screens has to search for an existing booking first.

`quirk` changes the screens the way a vendor update might, for the
connector's tests: a renamed label, reordered fields, a field the ERP
silently truncates, or a form that posts to another host.
"""

import html
import re
import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from mock_erp import db
from mock_erp.bookings import Booking, book

# (field, label) in the order the form shows them.
FIELDS = (
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
    # 380 Rechnung, 381 Gutschrift; the invoice a credit note corrects.
    ("document_type", "Belegart"),
    ("preceding_invoice", "Bezugsrechnung"),
)
DATES = {"issue_date", "due_date"}
AMOUNTS = {"net_total", "vat_total", "gross_total", "amount_due"}
EMPTY = "—"
QUIRKS = {"renamed", "reordered", "truncating", "foreign_action"}
_GERMAN_AMOUNT = re.compile(r"-?\d{1,3}(\.\d{3})*,\d{2}|-?\d+,\d{2}")


def german_amount(value: Decimal) -> str:
    whole, cents = f"{value:.2f}".split(".")
    sign = "-" if whole.startswith("-") else ""
    digits = whole.lstrip("-")
    grouped = ".".join(reversed([digits[max(0, i - 3) : i] for i in range(len(digits), 0, -3)]))
    return f"{sign}{grouped},{cents}"


def parse_german_amount(text: str) -> Decimal:
    if not _GERMAN_AMOUNT.fullmatch(text):
        raise ValueError(f"{text!r} is not an amount like 1.234,56")
    return Decimal(text.replace(".", "").replace(",", "."))


def parse_german_date(text: str) -> date:
    return datetime.strptime(text, "%d.%m.%Y").date()


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang='de'><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)} – Mock-ERP</title></head>"
        f"<body><nav><a href='/ui/bookings'>Buchungen</a> · "
        f"<a href='/ui/bookings/new'>Neue Buchung</a></nav>{body}</body></html>",
        status_code=status,
    )


def _shown(field: str, row: db.BookedInvoiceRow) -> str:
    value = {
        "net_total": row.net_cents,
        "vat_total": row.vat_cents,
        "gross_total": row.gross_cents,
        "amount_due": row.amount_due_cents,
    }.get(field, getattr(row, field, None))
    if value is None:
        return EMPTY
    if field in AMOUNTS:
        return german_amount(Decimal(value) / 100)
    if field in DATES:
        return value.strftime("%d.%m.%Y")
    return str(value)


def add_ui(app: FastAPI, engine: Engine, quirk: str | None = None) -> None:
    if quirk is not None and quirk not in QUIRKS:
        raise ValueError(f"unknown quirk {quirk!r}")
    fields = list(reversed(FIELDS)) if quirk == "reordered" else list(FIELDS)
    if quirk == "renamed":
        fields = [(name, "Summe netto" if name == "net_total" else label) for name, label in fields]
    action = (
        "https://elsewhere.example/ui/bookings" if quirk == "foreign_action" else "/ui/bookings"
    )

    def form(values: dict[str, str], problem: str | None = None) -> str:
        rows = "".join(
            f"<p><label for='{name}'>{html.escape(label)}</label> "
            f"<input id='{name}' name='{name}' value='{html.escape(values.get(name, ''))}'></p>"
            for name, label in fields
        )
        alert = "" if problem is None else f"<p role='alert'>{html.escape(problem)}</p>"
        return (
            f"<h1>Neue Buchung</h1>{alert}<form method='post' action='{action}'>{rows}"
            "<p><button type='submit'>Buchen</button></p></form>"
        )

    @app.get("/ui", include_in_schema=False)
    def ui_home() -> RedirectResponse:
        return RedirectResponse("/ui/bookings")

    @app.get("/ui/bookings", include_in_schema=False)
    def ui_bookings(reference: str | None = None) -> HTMLResponse:
        with Session(engine) as active:
            query = select(db.BookedInvoiceRow).order_by(db.BookedInvoiceRow.id)
            if reference:
                query = query.where(db.BookedInvoiceRow.reference == reference)
            rows = list(active.scalars(query))
        items = "".join(
            f"<tr><td><a href='/ui/bookings/{row.id}'>Buchung {row.id}</a></td>"
            f"<td>{html.escape(row.number)}</td><td>{html.escape(row.reference)}</td></tr>"
            for row in rows
        )
        search = (
            "<form method='get' action='/ui/bookings'><label for='reference'>Ihre Referenz</label> "
            f"<input id='reference' name='reference' value='{html.escape(reference or '')}'> "
            "<button type='submit'>Suchen</button></form>"
        )
        table = (
            f"<table aria-label='Buchungen'><thead><tr><th>Buchung</th><th>Rechnungsnummer</th>"
            f"<th>Ihre Referenz</th></tr></thead><tbody>{items}</tbody></table>"
            if rows
            else "<p>Keine Buchungen gefunden.</p>"
        )
        return _page("Buchungen", f"<h1>Buchungen</h1>{search}{table}")

    @app.get("/ui/bookings/new", include_in_schema=False)
    def ui_new() -> HTMLResponse:
        return _page("Neue Buchung", form({}))

    @app.post("/ui/bookings", include_in_schema=False)
    async def ui_book(request: Request):
        submitted = {name: str(value).strip() for name, value in (await request.form()).items()}
        try:
            parsed: dict = {}
            for name, _ in FIELDS:
                text = submitted.get(name, "")
                if not text:
                    parsed[name] = None
                elif name in AMOUNTS:
                    parsed[name] = parse_german_amount(text)
                elif name in DATES:
                    parsed[name] = parse_german_date(text)
                else:
                    parsed[name] = text
            if quirk == "truncating" and parsed["number"]:
                parsed["number"] = parsed["number"][:8]  # an old field length
            # An empty "Belegart" is an invoice, as before the field existed.
            parsed["document_type"] = parsed.get("document_type") or "380"
            booking = Booking(**parsed)
        except (ValueError, ValidationError) as exc:
            return _page("Neue Buchung", form(submitted, f"Eingabe ungültig: {exc}"), status=422)
        with Session(engine) as active:
            try:
                booked, _ = book(active, booking, f"form-{uuid.uuid4().hex}", channel="form")
            except HTTPException as exc:
                return _page(
                    "Neue Buchung", form(submitted, str(exc.detail)), status=exc.status_code
                )
        return RedirectResponse(f"/ui/bookings/{booked['id']}", status_code=303)

    @app.get("/ui/bookings/{booking_id}", include_in_schema=False)
    def ui_booking(booking_id: int) -> HTMLResponse:
        with Session(engine) as active:
            row = active.get(db.BookedInvoiceRow, booking_id)
            if row is None:
                return _page("Nicht gefunden", "<h1>Buchung nicht gefunden</h1>", status=404)
            items = "".join(
                f"<dt>{html.escape(label)}</dt><dd>{html.escape(_shown(name, row))}</dd>"
                for name, label in FIELDS
            )
        return _page(
            f"Buchung {booking_id}",
            f"<h1>Buchung {booking_id}</h1><dl aria-label='Buchung'>{items}</dl>",
        )
