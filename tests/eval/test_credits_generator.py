"""The credit-note set (eval/generators/credits.py)."""

import re

import pytest

from app.extract.confidence import document_text
from app.models import DocumentFormat
from app.parse.dispatch import parse_invoice
from eval.generators.credits import (
    CII,
    FORMATS,
    INVOICE_NOT_ON_FILE,
    NAMES_INVOICE,
    NO_LINK,
    OFFSET,
    PARTIAL,
    PDF,
    PDF_NEGATIVE,
    REFERENCE_REFORMATTED,
    REFUND_AMOUNT,
    REFUND_REFERENCE,
    SETTLEMENTS,
    UBL,
    UNKNOWN_REFERENCE,
    VARIANTS,
    build_credit_set,
    reformatted,
)
from eval.generators.world import build_world


def _key(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.casefold())


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def credit_set(world):
    return build_credit_set(world)


@pytest.fixture(scope="module")
def invoices(world):
    return {entry.id: entry for entry in world.invoices}


def test_every_variant_settlement_and_format_occurs_in_both_splits(credit_set):
    for split in ("dev", "test"):
        cases = [case for case in credit_set.cases if case.split == split]
        assert {case.variant for case in cases} == set(VARIANTS)
        assert {case.format for case in cases} == set(FORMATS)
        assert {case.settlement for case in cases} == set(SETTLEMENTS)


def test_same_seed_gives_the_same_files(world, credit_set):
    again = build_credit_set(world)
    assert [case.content for case in again.cases] == [case.content for case in credit_set.cases]


def test_every_case_credits_another_invoice(credit_set):
    credited = [case.base_id for case in credit_set.cases]
    offset = [case.offset_invoice_id for case in credit_set.cases if case.offset_invoice_id]
    assert len(set(credited + offset)) == len(credited) + len(offset)


def test_credit_notes_are_381_with_positive_amounts_and_no_order(credit_set):
    for case in credit_set.cases:
        credit = case.credit
        assert credit.type_code == "381"
        assert credit.po_ref is None
        assert credit.gross_total > 0
        assert all(item.net > 0 for item in credit.items)


def test_a_partial_credit_takes_back_less_than_its_invoice(credit_set, invoices):
    for case in credit_set.cases:
        invoice = invoices[case.base_id].invoice
        if case.variant in PARTIAL:
            assert case.credit.gross_total < invoice.gross_total
        else:
            assert case.credit.gross_total == invoice.gross_total


def test_what_a_credit_note_names(credit_set, invoices, world):
    numbers = {_key(entry.invoice.number) for entry in world.invoices}
    for case in credit_set.cases:
        number = invoices[case.base_id].invoice.number
        reference = case.credit.preceding_ref
        if case.variant == REFERENCE_REFORMATTED:
            assert reference != number
            assert _key(reference) == _key(number)
        elif case.variant in NAMES_INVOICE:
            assert reference == number
        elif case.variant == UNKNOWN_REFERENCE:
            assert _key(reference) not in numbers
        else:
            assert reference is None


def test_reformatting_changes_the_writing_not_the_number():
    for number in ("RE-2026-04319", "2026/1450", "R26004651", "INV-004884"):
        assert reformatted(number) != number
        assert _key(reformatted(number)) == _key(number)


def test_only_the_cases_with_a_right_answer_of_no_link_expect_none(credit_set):
    for case in credit_set.cases:
        assert (case.expected_invoice_id is None) == (case.variant in NO_LINK)


def test_an_invoice_that_never_arrived_is_not_on_file(credit_set):
    on_file = {entry.id for entry in credit_set.on_file}
    for case in credit_set.cases:
        assert (case.base_id in on_file) == (case.variant != INVOICE_NOT_ON_FILE)


def test_settlements_are_transfers_in_the_erp(credit_set, invoices):
    transactions = {tx.id: tx for tx in credit_set.world.bank_transactions}
    assert len(transactions) == len(credit_set.world.bank_transactions)
    for case in credit_set.cases:
        total = case.credit.gross_total
        if case.settlement in (REFUND_REFERENCE, REFUND_AMOUNT):
            (payment,) = case.payments
            assert transactions[payment.bank_transaction_id].amount == -total
            assert payment.amount == total
        elif case.settlement == OFFSET:
            (payment,) = case.payments
            tx = transactions[payment.bank_transaction_id]
            partner = invoices[case.offset_invoice_id].invoice
            assert tx.amount == partner.gross_total - total
            assert tx.booking_date > max(case.credit.issue_date, partner.issue_date)
            assert sum(p.amount for p in case.offset_payments) == partner.gross_total
        else:
            assert case.payments == ()


def test_the_xml_credit_notes_parse_to_what_was_generated(credit_set):
    for case in credit_set.cases:
        if case.format not in (UBL, CII):
            continue
        source = DocumentFormat.UBL if case.format == UBL else DocumentFormat.CII
        assert parse_invoice(case.content) == case.credit.to_gold(source)


def test_a_pdf_credit_note_prints_its_number_the_invoice_and_its_sign(credit_set):
    for case in credit_set.cases:
        if case.format not in (PDF, PDF_NEGATIVE):
            continue
        text = document_text(case.content)
        assert case.credit.number in text
        if case.credit.preceding_ref:
            assert case.credit.preceding_ref in text
        # A minus before an amount, not inside a number or a date.
        negative = re.search(r"(?<![\w/-])-\d", text) is not None
        assert negative == (case.format == PDF_NEGATIVE)
