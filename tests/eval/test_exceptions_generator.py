from collections import Counter
from decimal import Decimal

import pytest

from app.checks import arithmetic
from app.checks.rules import german_vat_id_checksum_holds
from app.extract.confidence import valid_iban
from app.extract.draft import InvoiceDraft
from app.models import DocumentFormat
from eval.generators.exceptions import (
    CLEAN,
    DUPLICATE,
    IBAN,
    KINDS,
    MISSING_PO,
    PRICE,
    TOTALS,
    WRONG_VAT,
    build_exception_set,
)
from eval.generators.world import build_world


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def exceptions(world):
    return build_exception_set(world)


def cases_of(exceptions, kind, variant=None):
    return [
        case
        for case in exceptions.cases
        if case.kind == kind and (variant is None or case.variant == variant)
    ]


def broken_rules(case) -> set[str]:
    draft = InvoiceDraft.model_validate(case.invoice.to_gold(DocumentFormat.UNKNOWN).model_dump())
    return {result.rule for result in arithmetic.evaluate(draft) if not result.holds}


def order_of(world, case):
    entry = next(entry for entry in world.invoices if entry.id == case.base_id)
    return next(order for order in world.orders if order.number == entry.po_number)


def test_set_has_twenty_cases_of_each_kind_and_as_many_clean_ones(exceptions):
    assert Counter(case.kind for case in exceptions.cases) == {
        **dict.fromkeys(KINDS, 20),
        CLEAN: 120,
    }


def test_each_kind_is_half_dev_half_test(exceptions):
    assert {
        (kind, split): count
        for (kind, split), count in Counter(
            (case.kind, case.split) for case in exceptions.cases
        ).items()
    } == {(kind, split): 10 for kind in KINDS for split in ("dev", "test")} | {
        (CLEAN, "dev"): 60,
        (CLEAN, "test"): 60,
    }


def test_same_seed_gives_the_same_set(world, exceptions):
    assert build_exception_set(world) == exceptions


def test_another_seed_gives_another_set(world, exceptions):
    assert build_exception_set(world, seed=7).cases != exceptions.cases


def test_every_kind_has_more_than_one_variant(exceptions):
    variants = {kind: {case.variant for case in cases_of(exceptions, kind)} for kind in KINDS}
    assert all(len(found) > 1 for found in variants.values()), variants


def test_only_a_duplicate_is_made_from_an_invoice_on_file(exceptions):
    on_file = {entry.id for entry in exceptions.on_file}
    assert {case.kind for case in exceptions.cases if case.base_id in on_file} == {DUPLICATE}


def test_no_invoice_is_used_for_two_cases(exceptions):
    assert max(Counter(case.base_id for case in exceptions.cases).values()) == 1


def test_clean_invoice_breaks_no_arithmetic_rule(exceptions):
    assert [case.id for case in cases_of(exceptions, CLEAN) if broken_rules(case)] == []


def test_totals_case_breaks_an_arithmetic_rule(exceptions):
    assert [case.id for case in cases_of(exceptions, TOTALS) if not broken_rules(case)] == []


@pytest.mark.parametrize("kind", [WRONG_VAT, PRICE, IBAN, MISSING_PO, DUPLICATE])
def test_other_kinds_keep_their_arithmetic_consistent(exceptions, kind):
    """Each case carries one planted problem, so a detection can be told
    from a side effect."""
    assert [case.id for case in cases_of(exceptions, kind) if broken_rules(case)] == []


def test_wrong_rate_is_not_a_german_vat_rate(exceptions):
    rates = {
        item.vat_rate
        for case in cases_of(exceptions, WRONG_VAT)
        if case.variant.startswith("rate")
        for item in case.invoice.items
    }
    assert rates - {Decimal(7), Decimal(19)}


def test_wrong_vat_id_fails_shape_or_check_digit(exceptions):
    ids = [
        case.invoice.seller.vat_id
        for case in cases_of(exceptions, WRONG_VAT)
        if case.variant.startswith("vat_id")
    ]
    assert ids
    assert not [
        vat_id for vat_id in ids if len(vat_id) == 11 and german_vat_id_checksum_holds(vat_id)
    ]


def test_changed_account_is_a_valid_iban_the_supplier_does_not_have(world, exceptions):
    suppliers = {supplier.id: supplier for supplier in world.suppliers}
    by_id = {entry.id: entry for entry in world.invoices}
    for case in cases_of(exceptions, IBAN):
        known = suppliers[by_id[case.base_id].supplier_id].ibans
        new = [iban for iban in case.invoice.ibans if iban not in known]
        assert new, case.id
        assert all(valid_iban(iban) for iban in new), case.id


def test_raised_price_is_above_the_order_by_more_than_the_tolerance(world, exceptions):
    for case in cases_of(exceptions, PRICE):
        ordered = {line.description: line.unit_price for line in order_of(world, case).lines}
        raises = [
            (item.unit_price - ordered[item.name]) / ordered[item.name]
            for item in case.invoice.items
        ]
        assert max(raises) > Decimal("0.02"), case.id


def test_price_within_tolerance_stays_within_it(world, exceptions):
    cases = cases_of(exceptions, CLEAN, "price_within_tolerance")
    assert cases
    for case in cases:
        ordered = {line.description: line.unit_price for line in order_of(world, case).lines}
        raises = [
            (item.unit_price - ordered[item.name]) / ordered[item.name]
            for item in case.invoice.items
        ]
        assert Decimal(0) < max(raises) <= Decimal("0.02"), case.id


def test_clean_invoice_on_the_second_account_uses_an_account_on_file(world, exceptions):
    suppliers = {supplier.id: supplier for supplier in world.suppliers}
    by_id = {entry.id: entry for entry in world.invoices}
    cases = cases_of(exceptions, CLEAN, "second_account")
    assert cases
    for case in cases:
        known = suppliers[by_id[case.base_id].supplier_id].ibans
        assert case.invoice.ibans == known[1:2], case.id


def test_order_of_an_invoice_bought_without_one_is_not_in_the_erp(world, exceptions):
    in_erp = {order.number for order in exceptions.world.orders}
    by_id = {entry.id: entry for entry in world.invoices}
    for case in cases_of(exceptions, MISSING_PO):
        assert by_id[case.base_id].po_number not in in_erp, case.id
        assert case.invoice.po_ref not in in_erp, case.id


def test_erp_keeps_every_other_order(world, exceptions):
    assert len(exceptions.world.orders) == len(world.orders) - 20


def test_duplicate_keeps_supplier_and_amount_of_the_invoice_on_file(world, exceptions):
    by_id = {entry.id: entry for entry in world.invoices}
    for case in cases_of(exceptions, DUPLICATE):
        original = by_id[case.base_id].invoice
        assert (case.invoice.seller, case.invoice.gross_total) == (
            original.seller,
            original.gross_total,
        ), case.id
