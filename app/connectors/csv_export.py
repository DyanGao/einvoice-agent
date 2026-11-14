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
"""

import csv
import fcntl
import io
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.connectors.base import Booking, DeliveryFailed

ENCODING = "cp1252"
HEADER = (
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
_REFERENCE = HEADER.index("Referenz")


def _amount(value: str | None) -> str:
    if value is None:
        return ""
    return f"{Decimal(value):.2f}".replace(".", ",")


def _date(value: str | None) -> str:
    return "" if value is None else date.fromisoformat(value).strftime("%d.%m.%Y")


def row_of(booking: Booking) -> list[str]:
    return [
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


def _line(row: list[str]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=";", lineterminator="\r\n").writerow(row)
    return buffer.getvalue()


class CsvExportConnector:
    name = "csv_export"

    def __init__(self, folder: Path) -> None:
        self._folder = Path(folder)

    def deliver(self, booking: Booking, key: str) -> str:
        row = row_of(booking)
        try:
            encoded = _line(row).encode(ENCODING)
        except UnicodeEncodeError as exc:
            raise DeliveryFailed(
                f"invoice {booking.number}: {exc.object[exc.start : exc.end]!r} cannot be written "
                "in Windows-1252, the encoding of the booking list",
                retryable=False,
            ) from exc
        name = f"buchungen-{booking.issue_date[:7]}.csv"
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
            with (self._folder / name).open("a+b") as file:
                fcntl.flock(file, fcntl.LOCK_EX)
                return self._append(file, name, row, encoded)
        except OSError as exc:
            raise DeliveryFailed(
                f"booking list {self._folder / name} could not be written: {exc}", retryable=True
            ) from exc

    def _append(self, file, name: str, row: list[str], encoded: bytes) -> str:
        file.seek(0)
        existing = list(
            csv.reader(io.StringIO(file.read().decode(ENCODING), newline=""), delimiter=";")
        )
        for number, written in enumerate(existing[1:], start=2):
            if written[_REFERENCE] == row[_REFERENCE]:
                if written != row:
                    raise DeliveryFailed(
                        f"{name} line {number} already holds this invoice with other values",
                        retryable=False,
                    )
                return f"{name}:{number}"
        if not existing:
            file.write(_line(list(HEADER)).encode(ENCODING))
        file.write(encoded)
        file.flush()
        os.fsync(file.fileno())
        return f"{name}:{max(len(existing), 1) + 1}"
