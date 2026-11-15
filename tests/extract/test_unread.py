"""Values printed in a standard format that the reading left out (the gap that
three model runs found: a printed Leitweg-ID left out and booked anyway)."""

from datetime import date

from app.extract import InvoiceDraft, PartyDraft
from app.extract.confidence import printed_but_unread
from app.models import DocumentFormat

LEITWEG = "04011000-12345-03"  # KoSIT example, valid check digits
IBAN = "DE89370400440532013000"
OTHER_IBAN = "GB82WEST12345698765432"


def draft(**fields) -> InvoiceDraft:
    base = dict(
        number="RE-1",
        issue_date=date(2026, 3, 1),
        source_format=DocumentFormat.PDF_TEXT,
        seller=PartyDraft(name="Muster GmbH", vat_id="DE123456788"),
        buyer=PartyDraft(name="Nordlicht GmbH"),
        payment_ibans=(IBAN,),
    )
    return InvoiceDraft(**(base | fields))


def test_leitweg_id_on_the_page_but_not_read_is_reported():
    text = f"Rechnung RE-1\nKundenzeichen {LEITWEG}\nIBAN {IBAN}"
    assert printed_but_unread(draft(), text) == ("buyer_reference",)


def test_leitweg_id_that_was_read_is_not_reported():
    text = f"Rechnung RE-1 {LEITWEG} {IBAN}"
    assert printed_but_unread(draft(buyer_reference=LEITWEG), text) == ()


def test_a_date_is_not_taken_for_a_leitweg_id():
    """2026-01-21 has the shape; ISO dates must never count, even with lucky digits."""
    text = f"Datum 2026-01-21 fällig 2026-02-04 {IBAN}"
    assert printed_but_unread(draft(), text) == ()


def test_shape_without_valid_check_digits_is_not_a_leitweg_id():
    assert printed_but_unread(draft(), f"Ref 04011000-12345-04 {IBAN}") == ()


def test_iban_on_the_page_but_not_read_is_reported_even_when_spaced():
    text = "IBAN DE89 3704 0044 0532 0130 00"
    assert printed_but_unread(draft(payment_ibans=()), text) == ("payment_ibans",)


def test_second_iban_on_the_page_that_was_not_read_is_reported():
    text = f"IBAN {IBAN} oder {OTHER_IBAN}"
    assert printed_but_unread(draft(), text) == ("payment_ibans",)


def test_iban_with_wrong_check_digits_is_ignored():
    assert printed_but_unread(draft(payment_ibans=()), "IBAN DE00370400440532013000") == ()


def test_german_vat_id_read_for_neither_party_is_reported():
    text = f"USt-IdNr. DE123456788 Kunde DE811115368 {IBAN}"
    assert printed_but_unread(draft(), text) == ("buyer.vat_id",)


def test_sellers_vat_id_left_out_is_reported_for_the_seller():
    text = f"USt-IdNr. DE123456788 {IBAN}"
    assert printed_but_unread(draft(seller=PartyDraft(name="Muster GmbH")), text) == (
        "seller.vat_id",
    )


def test_vat_ids_read_for_seller_and_buyer_are_not_reported():
    buyer = PartyDraft(name="Nordlicht GmbH", vat_id="DE811115368")
    text = f"DE123456788 DE811115368 {IBAN}"
    assert printed_but_unread(draft(buyer=buyer), text) == ()


def test_no_text_no_report():
    assert printed_but_unread(draft(payment_ibans=()), None) == ()


def test_unread_fields_are_not_part_of_the_invoice_of_record():
    from eval.generators.structured import generate

    invoice = next(generate(1)).invoice
    complete = InvoiceDraft.model_validate(
        invoice.model_dump(mode="json") | {"unread_fields": ["buyer_reference"]}
    )
    assert complete.to_invoice() == invoice


def test_scoring_extractor_marks_what_was_printed_but_not_read():
    from app.extract.confidence import Calibration, ScoringExtractor

    class Reads:
        def extract(self, data):
            return draft()

    text = f"Rechnung {LEITWEG} {IBAN}"
    scored = ScoringExtractor(Reads(), Calibration.load(), text_of=lambda data: text).extract(b"")
    assert scored.unread_fields == ("buyer_reference",)
