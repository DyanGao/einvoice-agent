from datetime import date
from decimal import Decimal

import pytest

from app.extract import BaselineExtractor
from app.extract.baseline import extract_from_text, parse_amount, parse_date
from eval.generators.pdf import generate
from eval.score import wrong_fields

TEXT = """\
Muster Lieferant GmbH
Hauptstraße 1
10115 Berlin
USt-IdNr.: DE123456789

Beispiel Kunde AG                        Rechnungsnummer:   RE-2026-00042
Gartenweg 5                              Rechnungsdatum:    05.03.2026
20095 Hamburg                            Zahlbar bis:       19.03.2026
Ihre USt-IdNr.: DE987654321              Bestellnummer:     PO-123456

Rechnung

Pos.   Bezeichnung               Menge      Einzelpreis    USt.      Gesamt
1      Beratung vor Ort          2,50 Std   100,00         19 %      250,00
2      Kopierpapier A4           10 Stk     5,00           7 %       50,00

                                            Nettobetrag              300,00 €
                                zzgl. 19 % USt. auf 250,00            47,50 €
                                 zzgl. 7 % USt. auf 50,00              3,50 €
                                           Gesamtbetrag              351,00 €
                                              Zu zahlen              351,00 €

IBAN: DE79 0000 0000 1234 5678 90
"""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("1234,56", "1234.56"),
        ("0,50", "0.50"),
        ("12", "12"),
        ("-100,00", "-100.00"),
    ],
)
def test_amount_formats(text, expected):
    assert parse_amount(text) == Decimal(expected)


def test_text_that_is_not_an_amount_gives_none():
    assert parse_amount("zwölf") is None


@pytest.mark.parametrize(
    "text",
    ["05.03.2026", "2026-03-05", "05/03/2026", "5. März 2026", "March 5, 2026", "am 5.3.2026"],
)
def test_date_formats(text):
    assert parse_date(text) == date(2026, 3, 5)


@pytest.mark.parametrize("text", ["31.02.2026", "kein Datum", "5. Brumaire 2026"])
def test_impossible_or_unknown_dates_give_none(text):
    assert parse_date(text) is None


def test_header_fields_are_read_from_labels():
    draft = extract_from_text(TEXT)
    assert (draft.number, draft.issue_date, draft.due_date, draft.po_ref) == (
        "RE-2026-00042",
        date(2026, 3, 5),
        date(2026, 3, 19),
        "PO-123456",
    )


def test_first_vat_id_is_the_seller_second_the_buyer():
    draft = extract_from_text(TEXT)
    assert (draft.seller.vat_id, draft.buyer.vat_id) == ("DE123456789", "DE987654321")


def test_party_names_come_from_block_positions():
    draft = extract_from_text(TEXT)
    assert (draft.seller.name, draft.buyer.name) == ("Muster Lieferant GmbH", "Beispiel Kunde AG")


def test_totals_are_read():
    draft = extract_from_text(TEXT)
    assert (draft.net_total, draft.vat_total, draft.gross_total, draft.amount_due) == (
        Decimal("300.00"),
        Decimal("51.00"),
        Decimal("351.00"),
        Decimal("351.00"),
    )


def test_line_items_are_read():
    first, second = extract_from_text(TEXT).lines
    assert (first.description, first.quantity, first.unit_price, first.net, first.vat_rate) == (
        "Beratung vor Ort",
        Decimal("2.50"),
        Decimal("100.00"),
        Decimal("250.00"),
        Decimal("19"),
    )
    assert second.line_id == "2"


def test_vat_groups_are_read():
    groups = extract_from_text(TEXT).vat_breakdown
    assert [(g.rate, g.taxable_amount, g.tax_amount) for g in groups] == [
        (Decimal("19"), Decimal("250.00"), Decimal("47.50")),
        (Decimal("7"), Decimal("50.00"), Decimal("3.50")),
    ]


def test_iban_is_compacted():
    assert extract_from_text(TEXT).payment_ibans == ("DE79000000001234567890",)


def test_iban_with_a_wrong_check_digit_is_dropped():
    broken = TEXT.replace("DE79 0000", "DE78 0000")
    assert extract_from_text(broken).payment_ibans == ()


def test_unknown_label_leaves_the_field_empty_instead_of_guessing():
    draft = extract_from_text(TEXT.replace("Rechnungsnummer:", "Vorgangskennung:"))
    assert draft.number is None


def test_empty_text_gives_an_empty_draft():
    assert extract_from_text("").number is None


@pytest.mark.parametrize("data", [b"", b"not a pdf", b"%PDF-1.7\ngarbage"])
def test_unreadable_input_gives_an_empty_draft_without_raising(data):
    assert BaselineExtractor().extract(data).number is None


def test_baseline_reads_the_dev_layouts_it_was_written_for():
    # Regression floor, not a quality claim: the rules were written while
    # looking at these layouts. Held-out layouts are scored by the eval only.
    wrong = compared = 0
    for document in generate(32):
        if document.split != "dev":
            continue
        errors, total = wrong_fields(
            document.invoice, BaselineExtractor().extract(document.content)
        )
        wrong, compared = wrong + len(errors), compared + total
    assert 1 - wrong / compared >= 0.98
