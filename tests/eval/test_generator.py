import random
from decimal import Decimal

import pytest

from app import safe_xml
from app.models import DocumentFormat
from app.parse import parse_invoice
from eval.generators.canonical import DEFECTS, EN16931, XRECHNUNG, build_invoice, german_iban
from eval.generators.structured import generate
from eval.generators.xrechnung import render_cii, render_ubl

COUNT = 25
DOCUMENTS = list(generate(COUNT))


def test_three_files_per_invoice():
    assert len(DOCUMENTS) == COUNT * 3


def test_same_seed_gives_byte_identical_files():
    again = list(generate(COUNT))
    assert [d.content for d in again] == [d.content for d in DOCUMENTS]


def test_different_seed_gives_different_invoices():
    other = next(generate(1, seed=1))
    assert other.content != DOCUMENTS[0].content


def test_invoice_does_not_depend_on_set_size():
    assert build_invoice(7, 3) == build_invoice(7, 3)
    assert list(generate(2))[:3] == DOCUMENTS[:3]


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda d: d.doc_id)
def test_every_rendering_parses_back_to_its_gold(document):
    assert parse_invoice(document.content) == document.invoice


def test_clean_invoice_totals_are_consistent():
    invoice = build_invoice(1, 0)
    assert (
        invoice.line_net_total,
        invoice.vat_total,
        invoice.gross_total,
    ) == (
        sum(item.net for item in invoice.items),
        sum(group.tax_amount for group in invoice.tax_groups),
        invoice.net_total + invoice.vat_total,
    )


def test_tax_amount_is_rounded_half_up_to_cents():
    for index in range(50):
        for group in build_invoice(1, index).tax_groups:
            exact = group.taxable_amount * group.rate / 100
            assert abs(group.tax_amount - exact) <= Decimal("0.005")


def test_generated_iban_has_a_valid_check_digit():
    iban = german_iban(random.Random(1))  # noqa: S311
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    assert (len(iban), int(digits) % 97) == (22, 1)


def test_one_invoice_in_five_carries_a_defect():
    defective = {d.doc_id[:8] for d in DOCUMENTS if d.defect}
    assert len(defective) == COUNT // 5


def test_all_defect_types_occur():
    assert {d.defect for d in DOCUMENTS if d.defect} == {defect.name for defect in DEFECTS}


def test_defective_xrechnung_is_expected_invalid_with_its_rule():
    document = next(d for d in DOCUMENTS if d.defect == "gross_total_off")
    assert (document.expected_valid, document.expected_rule) == (False, "BR-CO-15")


def test_xrechnung_only_defect_leaves_plain_en16931_valid():
    document = next(
        d for d in DOCUMENTS if d.defect == "missing_buyer_reference" and d.profile == EN16931
    )
    assert (document.expected_valid, document.expected_rule) == (True, None)


def test_gold_of_a_defective_invoice_describes_the_defective_document():
    document = next(d for d in DOCUMENTS if d.defect == "missing_buyer_reference")
    assert document.invoice.buyer_reference is None


def test_gold_carries_the_format_of_its_file():
    assert {d.invoice.source_format for d in DOCUMENTS} == {
        DocumentFormat.UBL,
        DocumentFormat.CII,
        DocumentFormat.ZUGFERD_PDF,
    }


@pytest.mark.parametrize("render", [render_ubl, render_cii])
def test_markup_characters_in_names_are_escaped(render):
    index = next(i for i in range(200) if "<" in build_invoice(1, i).buyer.name)
    invoice = build_invoice(1, index)
    root = safe_xml.parse(render(invoice, XRECHNUNG))
    assert invoice.buyer.name in "".join(root.itertext())
