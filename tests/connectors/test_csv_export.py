"""The DATEV-style CSV export: one booking list per accounting month."""

from dataclasses import replace
from pathlib import Path

import pytest

from app.connectors import Booking, CsvExportConnector, DeliveryFailed

GOLDEN = Path(__file__).parents[1] / "fixtures" / "export" / "buchungen-2026-03.csv"

FIRST = Booking(
    supplier_id="S001",
    number="RE-2026-00042",
    issue_date="2026-03-01",
    due_date="2026-03-31",
    currency="EUR",
    net_total="1234.50",
    vat_total="234.56",
    gross_total="1469.06",
    amount_due="1469.06",
    po_number="PO-123456",
    iban="DE89370400440532013000",
    reference="doc-1",
)
SECOND = Booking(
    supplier_id="S002",
    number="2026/0007",
    issue_date="2026-03-15",
    due_date=None,
    currency="EUR",
    net_total="10",
    vat_total=None,
    gross_total="10.00",
    amount_due="10.00",
    po_number=None,
    iban=None,
    reference="doc-2",
)


# A credit note taking back part of FIRST: positive amounts, "S" on the
# supplier's account, and the invoice it corrects (v1.0 Stage 5).
CREDIT = Booking(
    supplier_id="S001",
    number="GS-2026-00003",
    issue_date="2026-03-20",
    due_date=None,
    currency="EUR",
    net_total="100.00",
    vat_total="19.00",
    gross_total="119.00",
    amount_due="119.00",
    po_number=None,
    iban=None,
    reference="doc-3",
    document_type="381",
    preceding_invoice="RE-2026-00042",
)


def test_export_matches_the_golden_file_byte_for_byte(tmp_path):
    """Decimal comma, DD.MM.YYYY, Windows-1252, semicolons, CRLF, one header;
    an invoice is "H", a credit note "S" with its invoice."""
    export = CsvExportConnector(tmp_path)
    export.deliver(FIRST, "k1")
    export.deliver(SECOND, "k2")
    export.deliver(CREDIT, "k3")
    assert (tmp_path / GOLDEN.name).read_bytes() == GOLDEN.read_bytes()


def test_reference_names_the_file_and_line(tmp_path):
    export = CsvExportConnector(tmp_path)
    assert (export.deliver(FIRST, "k1"), export.deliver(SECOND, "k2")) == (
        "buchungen-2026-03.csv:2",
        "buchungen-2026-03.csv:3",
    )


def test_delivering_the_same_booking_again_writes_no_second_row(tmp_path):
    """A retry after a crash between writing and recording."""
    export = CsvExportConnector(tmp_path)
    first = export.deliver(FIRST, "k1")
    assert (export.deliver(FIRST, "k1"), len(GOLDEN.read_bytes().splitlines())) == (first, 4)
    assert len((tmp_path / GOLDEN.name).read_bytes().splitlines()) == 2


def test_same_invoice_with_other_content_is_refused(tmp_path):
    export = CsvExportConnector(tmp_path)
    export.deliver(FIRST, "k1")
    with pytest.raises(DeliveryFailed) as refused:
        export.deliver(replace(FIRST, gross_total="1470.00"), "k1")
    assert refused.value.retryable is False


def test_each_month_has_its_own_list(tmp_path):
    export = CsvExportConnector(tmp_path)
    export.deliver(FIRST, "k1")
    export.deliver(replace(SECOND, issue_date="2026-04-02"), "k2")
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "buchungen-2026-03.csv",
        "buchungen-2026-04.csv",
    ]


def test_text_windows_1252_cannot_hold_is_refused_not_mangled(tmp_path):
    with pytest.raises(DeliveryFailed, match="Windows-1252") as refused:
        CsvExportConnector(tmp_path).deliver(replace(FIRST, number="RE-ğ-1"), "k1")
    assert (refused.value.retryable, list(tmp_path.iterdir())) == (False, [])


def test_semicolon_in_a_value_is_quoted(tmp_path):
    CsvExportConnector(tmp_path).deliver(replace(FIRST, po_number="PO;1"), "k1")
    assert ';"PO;1";' in (tmp_path / GOLDEN.name).read_text(encoding="cp1252")


def test_export_folder_is_created_when_missing(tmp_path):
    CsvExportConnector(tmp_path / "exports" / "datev").deliver(FIRST, "k1")
    assert (tmp_path / "exports" / "datev" / GOLDEN.name).exists()


def test_folder_that_cannot_be_written_is_retried(tmp_path):
    blocker = tmp_path / "taken"
    blocker.write_text("a file, not a folder")
    with pytest.raises(DeliveryFailed) as failed:
        CsvExportConnector(blocker).deliver(FIRST, "k1")
    assert failed.value.retryable is True


def test_a_credit_note_delivered_again_writes_no_second_row(tmp_path):
    export = CsvExportConnector(tmp_path)
    export.deliver(FIRST, "k1")
    first = export.deliver(CREDIT, "k3")
    assert export.deliver(CREDIT, "k3") == first
    assert len((tmp_path / GOLDEN.name).read_bytes().splitlines()) == 3


def test_a_file_begun_in_the_old_format_keeps_it_and_refuses_a_credit_note(tmp_path):
    from app.connectors.csv_export import OLD_HEADER

    old_row = "01.03.2026;RE-0;S009;1,00;;1,00;1,00;EUR;;;;doc-0"
    (tmp_path / GOLDEN.name).write_bytes(
        (";".join(OLD_HEADER) + "\r\n" + old_row + "\r\n").encode("cp1252")
    )
    export = CsvExportConnector(tmp_path)
    export.deliver(FIRST, "k1")
    rows = (tmp_path / GOLDEN.name).read_bytes().decode("cp1252").splitlines()
    # The invoice in the file's own twelve columns, as before.
    assert rows[2].split(";")[-1] == "doc-1"
    assert len(rows[2].split(";")) == len(OLD_HEADER)
    with pytest.raises(DeliveryFailed) as refused:
        export.deliver(CREDIT, "k3")
    assert not refused.value.retryable
    assert refused.value.args[0].code == "delivery.csv_old_format"
