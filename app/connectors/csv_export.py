"""A DATEV-style booking list: one CSV file per accounting month.

The format is what German bookkeeping imports expect, not DATEV's certified
EXTF format: semicolons, a decimal comma without thousands separators,
dates as DD.MM.YYYY, Windows-1252, CRLF, one header row. Each invoice's
bookings go into the file of its invoice month, `buchungen-YYYY-MM.csv`, so
every attempt for the same invoice lands in the same file.

Exactly once: before appending, the file is searched for the invoice's
reference. Found with the same content, that row is the answer (a retry
after a crash between writing and recording); found with other content, the
booking is refused. Check and append happen under an exclusive file lock.

Credit notes (v1.0 Stage 5): amounts stay positive, as in DATEV, and the
last three columns say what a row is — "Belegart" (Rechnung, Gutschrift),
"Soll/Haben" on the supplier's account (an invoice H, a credit note S) and
"Bezugsrechnung", the invoice a credit note corrects. A month's file begun
before those columns existed keeps its twelve: an invoice is still written
there in that form, and a credit note, which it cannot express, is refused
until the file is moved aside.
"""

import csv
import fcntl
import io
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.connectors.base import Booking, DeliveryFailed
from app.texts import Text

ENCODING = "cp1252"
OLD_HEADER = (
    "Belegdatum",
    "Belegnummer",
    "Kreditor",
    "Nettobetrag",
    "Steuerbetrag",
    "Bruttobetrag",
    "Zahlbetrag",
    "Währung",
    "Fällig am",
    "Bestellnummer",
    "IBAN",
    "Referenz",
)
HEADER = (*OLD_HEADER, "Belegart", "Soll/Haben", "Bezugsrechnung")
_REFERENCE = HEADER.index("Referenz")
# On the supplier's account: an invoice is owed to it (Haben), a credit note
# owed by it (Soll).
_KIND = {False: ("Rechnung", "H"), True: ("Gutschrift", "S")}


def _amount(value: str | None) -> str:
    if value is None:
        return ""
    return f"{Decimal(value):.2f}".replace(".", ",")


def _date(value: str | None) -> str:
    return "" if value is None else date.fromisoformat(value).strftime("%d.%m.%Y")


def row_of(booking: Booking, header: tuple[str, ...] = HEADER) -> list[str]:
    """The booking as a row of a file with this header."""
    row = [
        _date(booking.issue_date),
        booking.number,
        booking.supplier_id,
        _amount(booking.net_total),
        _amount(booking.vat_total),
        _amount(booking.gross_total),
        _amount(booking.amount_due),
        booking.currency,
        _date(booking.due_date),
        booking.po_number or "",
        booking.iban or "",
        booking.reference,
    ]
    if header == OLD_HEADER:
        return row
    kind, side = _KIND[booking.is_credit_note]
    return [*row, kind, side, booking.preceding_invoice or ""]


def _line(row: list[str]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=";", lineterminator="\r\n").writerow(row)
    return buffer.getvalue()


def _encoded(booking: Booking, row: list[str]) -> bytes:
    try:
        return _line(row).encode(ENCODING)
    except UnicodeEncodeError as exc:
        raise DeliveryFailed(
            Text(
                "delivery.csv_encoding",
                number=booking.number,
                characters=repr(exc.object[exc.start : exc.end]),
            ),
            retryable=False,
        ) from exc


class CsvExportConnector:
    name = "csv_export"

    def __init__(self, folder: Path) -> None:
        self._folder = Path(folder)

    def deliver(self, booking: Booking, key: str) -> str:
        # Refused before the file is touched: text the encoding cannot hold.
        _encoded(booking, row_of(booking))
        name = f"buchungen-{booking.issue_date[:7]}.csv"
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
            with (self._folder / name).open("a+b") as file:
                fcntl.flock(file, fcntl.LOCK_EX)
                return self._append(file, name, booking)
        except OSError as exc:
            raise DeliveryFailed(
                Text("delivery.csv_not_written", file=str(self._folder / name), detail=str(exc)),
                retryable=True,
            ) from exc

    def _append(self, file, name: str, booking: Booking) -> str:
        file.seek(0)
        existing = list(
            csv.reader(io.StringIO(file.read().decode(ENCODING), newline=""), delimiter=";")
        )
        header = tuple(existing[0]) if existing else HEADER
        if header == OLD_HEADER and booking.is_credit_note:
            raise DeliveryFailed(
                Text("delivery.csv_old_format", file=name, number=booking.number),
                retryable=False,
            )
        row = row_of(booking, header)
        encoded = _encoded(booking, row)
        for number, written in enumerate(existing[1:], start=2):
            if written[_REFERENCE] == row[_REFERENCE]:
                if written != row:
                    raise DeliveryFailed(
                        Text("delivery.csv_conflict", file=name, line=number),
                        retryable=False,
                    )
                return f"{name}:{number}"
        if not existing:
            file.write(_line(list(HEADER)).encode(ENCODING))
        file.write(encoded)
        file.flush()
        os.fsync(file.fileno())
        return f"{name}:{max(len(existing), 1) + 1}"
