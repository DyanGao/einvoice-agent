from datetime import date
from decimal import Decimal

import pytest

from app.checks import (
    CheckContext,
    CheckSettings,
    FindingType,
    KnownInvoice,
    MasterData,
    PoLine,
    PurchaseOrder,
    Severity,
    Supplier,
    run_checks,
)
from app.checks.rules import (
    document_type,
    duplicates,
    edit_distance,
    german_vat_id_checksum_holds,
    purchase_order,
    supplier_and_iban,
    totals,
    vat_ids,
    vat_rates,
)
from app.extract import InvoiceDraft, LineDraft, PartyDraft, VatBreakdownDraft

IBAN = "DE89370400440532013000"
OTHER_IBAN = "DE02120300000000202051"
VAT_ID = "DE123456788"  # check digit valid; not a real company's number
SUPPLIER = Supplier(id="S1", name="Muster GmbH", vat_id=VAT_ID, ibans=(IBAN,))
ORDER = PurchaseOrder(number="PO-1", lines=(PoLine("Papier", Decimal("2"), Decimal("50.00")),))
NO_CONTEXT = CheckContext()
NO_ERP = CheckContext(master_data=MasterData.NOT_CONFIGURED)
ERP_DOWN = CheckContext(
    master_data=MasterData.UNAVAILABLE, master_data_problem="connection refused"
)
KNOWN = CheckContext(supplier=SUPPLIER, purchase_order=ORDER)


def line(**fields) -> LineDraft:
    base = dict(
        line_id="1",
        description="Papier",
        quantity=Decimal("2"),
        unit_price=Decimal("50.00"),
        net=Decimal("100.00"),
        vat_rate=Decimal("19"),
    )
    return LineDraft(**(base | fields))


def group(**fields) -> VatBreakdownDraft:
    base = dict(
        category="S",
        rate=Decimal("19"),
        taxable_amount=Decimal("100.00"),
        tax_amount=Decimal("19.00"),
    )
    return VatBreakdownDraft(**(base | fields))


def invoice(**fields) -> InvoiceDraft:
    """2 x 50.00 at 19 %, from a known supplier, on a known order."""
    base = dict(
        number="RE-100",
        issue_date=date(2026, 3, 1),
        type_code="380",
        currency="EUR",
        po_ref="PO-1",
        seller=PartyDraft(name="Muster GmbH", vat_id=VAT_ID),
        payment_ibans=(IBAN,),
        lines=(line(),),
        vat_breakdown=(group(),),
        line_net_total=Decimal("100.00"),
        net_total=Decimal("100.00"),
        vat_total=Decimal("19.00"),
        gross_total=Decimal("119.00"),
        amount_due=Decimal("119.00"),
    )
    return InvoiceDraft(**(base | fields))


def types(findings) -> list[FindingType]:
    return [finding.type for finding in findings]


def test_clean_invoice_from_a_known_supplier_on_a_known_order_has_no_findings():
    assert run_checks(invoice(), KNOWN) == []


# --- totals ---------------------------------------------------------------


def test_gross_that_is_not_net_plus_vat_is_reported_with_both_values():
    (finding,) = totals(invoice(gross_total=Decimal("129.00")), NO_CONTEXT)
    assert (finding.type, finding.fields, finding.detail) == (
        FindingType.TOTALS_MISMATCH,
        ("gross_total",),
        {"rule": "gross", "expected": "119.00", "actual": "129.00"},
    )


def test_total_off_by_one_cent_is_rounding_not_a_mismatch():
    assert totals(invoice(gross_total=Decimal("119.01")), NO_CONTEXT) == []


def test_vat_rounded_the_other_way_by_one_cent_is_not_a_mismatch():
    """19 % of 838.89 is 159.3891: 159.39 rounded, and 159.40 is one cent off."""
    draft = invoice(
        lines=(),
        vat_breakdown=(group(taxable_amount=Decimal("838.89"), tax_amount=Decimal("159.40")),),
        line_net_total=None,
        net_total=Decimal("838.89"),
        vat_total=Decimal("159.40"),
        gross_total=Decimal("998.29"),
        amount_due=None,
    )
    assert totals(draft, NO_CONTEXT) == []


def test_total_off_by_two_cents_is_a_mismatch():
    assert types(totals(invoice(gross_total=Decimal("119.02")), NO_CONTEXT)) == [
        FindingType.TOTALS_MISMATCH
    ]


def test_every_broken_rule_is_reported_separately():
    broken = invoice(lines=(line(net=Decimal("90.00")),), vat_total=Decimal("20.00"))
    rules = {finding.detail["rule"] for finding in totals(broken, NO_CONTEXT)}
    assert rules == {"line_amount", "line_sum", "vat_sum", "gross"}


def test_totals_that_cannot_be_checked_are_not_reported():
    assert totals(invoice(vat_total=None, vat_breakdown=()), NO_CONTEXT) == []


# --- VAT rates ------------------------------------------------------------


@pytest.mark.parametrize("rate", ["19", "7", "0"])
def test_german_rates_are_plausible(rate):
    rated = invoice(
        vat_breakdown=(group(rate=Decimal(rate)),), lines=(line(vat_rate=Decimal(rate)),)
    )
    assert vat_rates(rated, NO_CONTEXT) == []


def test_rate_germany_does_not_have_is_reported_for_the_group_and_the_line():
    odd = invoice(vat_breakdown=(group(rate=Decimal("20")),), lines=(line(vat_rate=Decimal("20")),))
    assert [finding.fields for finding in vat_rates(odd, NO_CONTEXT)] == [
        ("vat_breakdown.0.rate",),
        ("lines.0.vat_rate",),
    ]


@pytest.mark.parametrize(
    ("issued", "plausible"),
    [(date(2020, 6, 30), False), (date(2020, 7, 1), True), (date(2020, 12, 31), True),
     (date(2021, 1, 1), False)],
)  # fmt: skip
def test_sixteen_percent_was_a_german_rate_only_in_the_second_half_of_2020(issued, plausible):
    old = invoice(issue_date=issued, vat_breakdown=(group(rate=Decimal("16")),), lines=())
    assert (vat_rates(old, NO_CONTEXT) == []) is plausible


def test_foreign_sellers_rates_are_not_judged():
    foreign = invoice(
        seller=PartyDraft(name="Exemple SARL", vat_id="FR12345678901"),
        vat_breakdown=(group(rate=Decimal("20")),),
        lines=(),
    )
    assert vat_rates(foreign, NO_CONTEXT) == []


def test_reverse_charge_with_a_rate_is_implausible_whoever_the_seller_is():
    wrong = invoice(
        seller=PartyDraft(name="Exemple SARL", vat_id="FR12345678901"),
        vat_breakdown=(group(category="AE", rate=Decimal("19")),),
        lines=(),
    )
    assert types(vat_rates(wrong, NO_CONTEXT)) == [FindingType.VAT_RATE_IMPLAUSIBLE]


# --- VAT IDs --------------------------------------------------------------


def test_german_check_digit_algorithm_accepts_exactly_one_digit():
    valid = [d for d in range(10) if german_vat_id_checksum_holds(f"DE12345678{d}")]
    assert valid == [8]


@pytest.mark.parametrize(
    ("vat_id", "reason"),
    [
        ("DE123456789", "check digit"),
        ("DE12345678", "nine digits"),
        ("DE12345678X", "nine digits"),
        ("12345678", "shape"),
        ("FRABCDEFGH", "shape"),
    ],
)
def test_invalid_vat_id_is_reported_with_the_reason(vat_id, reason):
    (finding,) = vat_ids(invoice(seller=PartyDraft(name="X", vat_id=vat_id)), NO_CONTEXT)
    assert (finding.type, finding.fields, reason in finding.message) == (
        FindingType.VAT_ID_INVALID,
        ("seller.vat_id",),
        True,
    )


def test_buyer_vat_id_is_checked_too():
    bad = invoice(buyer=PartyDraft(name="Kunde AG", vat_id="DE123456789"))
    assert [finding.fields for finding in vat_ids(bad, NO_CONTEXT)] == [("buyer.vat_id",)]


def test_missing_or_well_formed_foreign_vat_ids_are_fine():
    fine = invoice(
        seller=PartyDraft(name="Exemple SARL", vat_id="FR12345678901"), buyer=PartyDraft()
    )
    assert vat_ids(fine, NO_CONTEXT) == []


# --- document type --------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [("380", []), (None, []), ("381", [FindingType.CREDIT_NOTE]),
     ("389", [FindingType.UNUSUAL_DOCUMENT_TYPE])],
)  # fmt: skip
def test_only_an_ordinary_invoice_passes_without_a_finding(code, expected):
    assert types(document_type(invoice(type_code=code), NO_CONTEXT)) == expected


# --- duplicates -----------------------------------------------------------


def on_file(**fields) -> CheckContext:
    base = dict(
        document_id="doc-1",
        seller_name="Muster GmbH",
        seller_vat_id=VAT_ID,
        number="RE-100",
        issue_date=date(2026, 3, 1),
        gross_total=Decimal("119.00"),
    )
    return CheckContext(known_invoices=(KnownInvoice(**(base | fields)),))


def test_same_supplier_and_number_is_a_duplicate_and_names_the_earlier_document():
    (finding,) = duplicates(invoice(), on_file())
    assert (finding.type, finding.detail["document_id"]) == (FindingType.DUPLICATE, "doc-1")


@pytest.mark.parametrize("number", ["re-100", "RE 100", "RE100", "Re-100."])
def test_number_written_differently_is_still_the_same_number(number):
    assert types(duplicates(invoice(number=number), on_file())) == [FindingType.DUPLICATE]


def test_same_number_from_another_supplier_is_not_a_duplicate():
    assert duplicates(invoice(), on_file(seller_vat_id="DE999999990", seller_name="Andere")) == []


def test_supplier_is_recognised_by_name_when_a_vat_id_is_missing():
    no_vat_id = invoice(seller=PartyDraft(name="MUSTER GmbH"))
    assert types(duplicates(no_vat_id, on_file())) == [FindingType.DUPLICATE]


@pytest.mark.parametrize(
    ("fields", "why"),
    [({"number": "RE-10O"}, "similar number"), ({"number": "2026-77"}, "date")],
    ids=["retyped-number", "same-day-same-amount"],
)
def test_same_supplier_and_amount_with_a_similar_number_or_the_same_date_is_a_near_duplicate(
    fields, why
):
    (finding,) = duplicates(invoice(**fields), on_file())
    assert (finding.type, why in finding.message) == (FindingType.NEAR_DUPLICATE, True)


@pytest.mark.parametrize(
    "fields",
    [
        {"number": "RE-101", "gross_total": Decimal("238.00")},
        {"number": "2026-77", "issue_date": date(2026, 4, 1)},
        # Two equal part deliveries, billed a week apart under consecutive numbers.
        {"number": "RE-101", "issue_date": date(2026, 3, 8)},
    ],
    ids=["next-number-other-amount", "same-amount-a-month-later", "next-number-same-amount"],
)
def test_the_suppliers_next_invoice_is_not_a_near_duplicate(fields):
    assert duplicates(invoice(**fields), on_file()) == []


def test_similar_number_and_same_amount_is_a_near_duplicate_when_a_date_is_unknown():
    undated = invoice(number="RE-10O", issue_date=None)
    assert types(duplicates(undated, on_file())) == [FindingType.NEAR_DUPLICATE]


def billed_before(**fields) -> CheckContext:
    known = on_file(**fields).known_invoices
    return CheckContext(known_invoices=known, delivery_id="LS-000007", delivery_billed_by=known)


def test_delivery_billed_before_under_another_number_and_date_is_reported():
    again = invoice(number="K-555", issue_date=date(2026, 3, 20))
    (finding,) = duplicates(again, billed_before())
    assert (finding.type, finding.severity, finding.detail) == (
        FindingType.DELIVERY_ALREADY_BILLED,
        Severity.REVIEW,
        {"document_id": "doc-1", "number": "RE-100", "delivery": "LS-000007"},
    )


def test_duplicate_is_not_reported_a_second_time_for_its_delivery():
    assert types(duplicates(invoice(), billed_before())) == [FindingType.DUPLICATE]


def test_edit_distance_counts_single_character_changes():
    assert (edit_distance("re100", "re100"), edit_distance("re100", "re10o"),
            edit_distance("re100", "re1001"), edit_distance("abc", "")) == (0, 1, 1, 3)  # fmt: skip


# --- supplier and IBAN ----------------------------------------------------


def test_iban_on_file_for_the_supplier_is_fine():
    assert supplier_and_iban(invoice(), KNOWN) == []


def test_iban_that_is_not_on_file_is_reported_and_always_needs_a_person():
    (finding,) = supplier_and_iban(invoice(payment_ibans=(OTHER_IBAN,)), KNOWN)
    assert (finding.type, finding.severity, finding.detail["unknown"]) == (
        FindingType.IBAN_CHANGED,
        Severity.REVIEW,
        [OTHER_IBAN],
    )


def test_one_new_iban_next_to_a_known_one_is_still_reported():
    both = invoice(payment_ibans=(IBAN, OTHER_IBAN))
    assert types(supplier_and_iban(both, KNOWN)) == [FindingType.IBAN_CHANGED]


def test_invoice_without_an_iban_from_a_known_supplier_is_fine():
    assert supplier_and_iban(invoice(payment_ibans=()), KNOWN) == []


def test_supplier_that_is_not_on_file_is_reported_instead_of_an_iban_change():
    assert types(supplier_and_iban(invoice(), NO_CONTEXT)) == [FindingType.UNKNOWN_SUPPLIER]


# --- purchase order -------------------------------------------------------


def test_invoice_without_an_order_needs_a_person_when_orders_are_required():
    (finding,) = purchase_order(invoice(po_ref=None), NO_CONTEXT)
    assert (finding.type, finding.severity) == (FindingType.MISSING_PO, Severity.REVIEW)


def test_invoice_without_an_order_is_only_noted_when_orders_are_optional():
    relaxed = CheckContext(settings=CheckSettings(po_required=False))
    (finding,) = purchase_order(invoice(po_ref=None), relaxed)
    assert (finding.type, finding.severity) == (FindingType.MISSING_PO, Severity.INFO)


def test_order_named_on_the_invoice_that_does_not_exist_is_reported():
    assert types(purchase_order(invoice(po_ref="PO-404"), NO_CONTEXT)) == [FindingType.PO_NOT_FOUND]


@pytest.mark.parametrize(
    ("price", "expected"),
    [("50.00", []), ("51.00", []), ("51.01", [FindingType.PRICE_DEVIATION]),
     ("56.00", [FindingType.PRICE_DEVIATION]), ("45.00", [])],
    ids=["as-ordered", "at-tolerance", "just-above", "twelve-percent", "cheaper"],
)  # fmt: skip
def test_unit_price_above_the_ordered_price_beyond_tolerance_is_reported(price, expected):
    priced = invoice(lines=(line(unit_price=Decimal(price)),))
    assert types(purchase_order(priced, KNOWN)) == expected


def test_price_deviation_says_by_how_much():
    (finding,) = purchase_order(invoice(lines=(line(unit_price=Decimal("56.00")),)), KNOWN)
    assert (finding.fields, finding.detail["above"], "12.0%" in finding.message) == (
        ("lines.0.unit_price",),
        "0.1200",
        True,
    )


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [("2", []), ("1", []), ("3", [FindingType.QUANTITY_DEVIATION])],
    ids=["as-ordered", "partial", "more-than-ordered"],
)
def test_quantity_above_the_ordered_quantity_is_reported(quantity, expected):
    billed = invoice(lines=(line(quantity=Decimal(quantity)),))
    assert types(purchase_order(billed, KNOWN)) == expected


def test_lines_are_compared_by_description_whatever_their_order():
    order = PurchaseOrder(
        number="PO-2",
        lines=(PoLine("Toner", Decimal("1"), Decimal("80.00")),
               PoLine("Papier", Decimal("2"), Decimal("50.00"))),
    )  # fmt: skip
    billed = invoice(lines=(line(), line(description="toner", quantity=Decimal("1"),
                                         unit_price=Decimal("80.00"))))  # fmt: skip
    assert purchase_order(billed, CheckContext(purchase_order=order)) == []


def test_line_that_is_not_on_the_order_is_not_compared_with_some_other_line():
    order = PurchaseOrder(
        number="PO-2",
        lines=(PoLine("Toner", Decimal("1"), Decimal("80.00")),
               PoLine("Papier", Decimal("2"), Decimal("50.00"))),
    )  # fmt: skip
    extra = invoice(lines=(line(description="Versand", unit_price=Decimal("500.00")),))
    assert purchase_order(extra, CheckContext(purchase_order=order)) == []


# --- no master data ---------------------------------------------------------


def test_without_an_erp_supplier_and_order_are_not_judged_and_one_note_says_so():
    (finding,) = run_checks(invoice(po_ref=None, payment_ibans=(OTHER_IBAN,)), NO_ERP)
    assert (finding.type, finding.severity) == (
        FindingType.MASTER_DATA_NOT_CHECKED,
        Severity.INFO,
    )


def test_an_erp_that_does_not_answer_sends_the_invoice_to_a_person():
    (finding,) = run_checks(invoice(), ERP_DOWN)
    assert (finding.type, finding.severity, "connection refused" in finding.message) == (
        FindingType.MASTER_DATA_NOT_CHECKED,
        Severity.REVIEW,
        True,
    )


def test_checks_that_need_no_erp_still_run_without_one():
    findings = run_checks(invoice(gross_total=Decimal("129.00")), NO_ERP)
    assert FindingType.TOTALS_MISMATCH in types(findings)
