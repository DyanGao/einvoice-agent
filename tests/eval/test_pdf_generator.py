from app.detect import detect
from app.extract.pdf_text import layout_text
from app.models import DocumentFormat
from eval.generators.pdf import generate
from eval.generators.pdf_layouts import (
    ALL_LAYOUTS,
    CREDIT_LABELS,
    HELD_OUT_LABELS,
    HELD_OUT_LAYOUTS,
    HELD_OUT_WORDING,
    LABELS,
    LAYOUTS,
)

COUNT = len(ALL_LAYOUTS) * 2
DOCUMENTS = list(generate(COUNT))


def test_same_seed_gives_byte_identical_pdfs():
    assert [d.content for d in generate(COUNT)] == [d.content for d in DOCUMENTS]


def test_every_layout_is_used():
    assert {d.layout for d in DOCUMENTS} == {layout.name for layout in ALL_LAYOUTS}


def test_there_are_at_least_ten_dev_layouts():
    assert len(LAYOUTS) >= 10


def test_layout_names_are_unique():
    assert len({layout.name for layout in ALL_LAYOUTS}) == len(ALL_LAYOUTS)


def test_test_split_is_exactly_the_held_out_layouts():
    held_out = {layout.name for layout in HELD_OUT_LAYOUTS}
    assert {d.layout for d in DOCUMENTS if d.split == "test"} == held_out
    assert {d.layout for d in DOCUMENTS if d.split == "dev"}.isdisjoint(held_out)


def test_held_out_wording_shares_no_label_with_dev_wording():
    for lang, keys in HELD_OUT_LABELS.items():
        for key, label in keys.items():
            assert label not in LABELS[lang][key], (lang, key)


def test_held_out_credit_wording_shares_no_label_with_dev_wording():
    for lang, keys in CREDIT_LABELS.items():
        for key, labels in keys.items():
            if key == "no_due_date":
                continue  # a sentence, not a label to find a value by
            assert labels[HELD_OUT_WORDING] not in labels[:HELD_OUT_WORDING], (lang, key)


def test_dev_layouts_never_use_held_out_wording_or_formats():
    held_out_only = {"short", "ch", "space", "footer", "stacked"}
    for layout in LAYOUTS:
        used = {layout.date_style, layout.number_style, layout.seller_position}
        assert layout.wording != 3
        assert used.isdisjoint(held_out_only), layout.name
        assert layout.meta_position != "stacked"


def test_every_pdf_is_detected_as_text_pdf():
    assert {detect(d.content) for d in DOCUMENTS} == {DocumentFormat.PDF_TEXT}


def test_invoice_number_and_parties_are_printed_on_every_pdf():
    for document in DOCUMENTS:
        text = layout_text(document.content)
        invoice = document.invoice
        assert invoice.number in text, document.doc_id
        assert invoice.seller.name in text, document.doc_id
        assert invoice.buyer.name in text, document.doc_id


def test_gold_marks_pdfs_as_having_no_validator_verdict():
    record = DOCUMENTS[0].gold_record()
    assert (record["format"], record["expected_valid"]) == ("pdf_text", None)
