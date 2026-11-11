"""The test suite ships each business case in both syntaxes. Two independent
parsers reading the same case must agree on every modelled field."""

import pytest

from app.parse import parse_invoice
from tests.parse import samples

SAME_CONTENT = ["01.01a", "01.05a", "01.07a", "01.14a", "01.18a", "01.19a", "01.20a", "03.06a"]
SAME_CONTENT.append("01.06_minimal")


def modelled_fields(xml: bytes) -> dict:
    return parse_invoice(xml).model_dump(exclude={"source_format"})


@pytest.mark.parametrize("case", SAME_CONTENT)
def test_ubl_and_cii_of_the_same_case_parse_identically(case):
    assert modelled_fields(samples.ubl(case)) == modelled_fields(samples.cii(case))


def test_placeholder_order_reference_is_the_only_difference_in_01_02a():
    # The UBL file fills the mandatory OrderReference/ID with "Dummywert" because
    # it only has a sales order number; the CII syntax needs no such filler.
    ubl, cii = modelled_fields(samples.ubl("01.02a")), modelled_fields(samples.cii("01.02a"))
    assert {k for k in ubl if ubl[k] != cii[k]} == {"po_ref"}
