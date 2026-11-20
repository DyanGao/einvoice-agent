"""One small function per rule. Each takes the invoice (as a draft, so that
it also works before review) and returns the findings it has, if any."""

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal

from app.checks import arithmetic
from app.checks.context import CheckContext, KnownInvoice, MasterData
from app.checks.finding import Finding, FindingType, Severity
from app.extract.complete import ZERO_RATE_CATEGORIES
from app.extract.draft import InvoiceDraft
from app.texts import Text

Check = Callable[[InvoiceDraft, CheckContext], list[Finding]]

_CENT = Decimal("0.01")


def totals(draft: InvoiceDraft, context: CheckContext) -> list[Finding]:
    return [
        Finding(
            type=FindingType.TOTALS_MISMATCH,
            severity=Severity.REVIEW,
            message=Text(
                "finding.totals_mismatch",
                check=result.description,
                expected_amount=result.expected.quantize(_CENT),
                actual_amount=result.actual,
            ),
            fields=result.fields,
            detail={
                "rule": result.rule,
                "expected": str(result.expected.quantize(_CENT)),
                "actual": str(result.actual),
            },
        )
        for result in arithmetic.evaluate(draft)
        if not result.holds
    ]


# --- VAT ------------------------------------------------------------------

_GERMAN_RATES = frozenset({Decimal(7), Decimal(19)})
# Temporary reduction, 1 July to 31 December 2020.
_GERMAN_RATES_2020_H2 = frozenset({Decimal(5), Decimal(16)})


def _german_rates(on: date | None) -> frozenset[Decimal]:
    if on is not None and date(2020, 7, 1) <= on <= date(2020, 12, 31):
        return _GERMAN_RATES | _GERMAN_RATES_2020_H2
    return _GERMAN_RATES


def _is_german(vat_id: str | None) -> bool:
    return vat_id is not None and vat_id.startswith("DE")


def vat_rates(draft: InvoiceDraft, context: CheckContext) -> list[Finding]:
    """Rates that German VAT law does not have. Only for German sellers:
    a foreign seller's rates follow rules this check does not know."""
    findings = []
    for index, group in enumerate(draft.vat_breakdown):
        if group.rate is not None and group.category in ZERO_RATE_CATEGORIES and group.rate != 0:
            findings.append(
                Finding(
                    type=FindingType.VAT_RATE_IMPLAUSIBLE,
                    severity=Severity.REVIEW,
                    message=Text(
                        "finding.vat_rate_category", category=group.category, rate=group.rate
                    ),
                    fields=(f"vat_breakdown.{index}.rate", f"vat_breakdown.{index}.category"),
                    detail={"rate": str(group.rate), "category": group.category},
                )
            )
    if not _is_german(draft.seller.vat_id):
        return findings
    allowed = _german_rates(draft.issue_date)
    rates = [
        (f"vat_breakdown.{index}.rate", group.rate)
        for index, group in enumerate(draft.vat_breakdown)
        if group.category == "S"
    ] + [(f"lines.{index}.vat_rate", line.vat_rate) for index, line in enumerate(draft.lines)]
    for path, rate in rates:
        if rate is not None and rate != 0 and rate not in allowed:
            findings.append(
                Finding(
                    type=FindingType.VAT_RATE_IMPLAUSIBLE,
                    severity=Severity.REVIEW,
                    message=Text("finding.vat_rate_not_german", rate=rate),
                    fields=(path,),
                    detail={"rate": str(rate), "allowed": sorted(map(str, allowed))},
                )
            )
    return findings


_VAT_ID_SHAPE = re.compile(r"[A-Z]{2}[A-Z0-9]{2,12}")
_GERMAN_VAT_ID = re.compile(r"DE\d{9}")


def german_vat_id_checksum_holds(vat_id: str) -> bool:
    """ISO 7064 MOD 11,10 over the nine digits of a German USt-IdNr."""
    product = 10
    for digit in vat_id[2:10]:
        total = (int(digit) + product) % 10 or 10
        product = (2 * total) % 11
    return (11 - product) % 10 == int(vat_id[10])


def _vat_id_problem(vat_id: str) -> Text | None:
    if vat_id.startswith("DE"):
        if not _GERMAN_VAT_ID.fullmatch(vat_id):
            return Text("vat_id.german_shape")
        if not german_vat_id_checksum_holds(vat_id):
            return Text("vat_id.german_check_digit")
        return None
    if not _VAT_ID_SHAPE.fullmatch(vat_id) or not any(ch.isdigit() for ch in vat_id):
        return Text("vat_id.eu_shape")
    return None


def vat_ids(draft: InvoiceDraft, context: CheckContext) -> list[Finding]:
    findings = []
    for role, party in (("seller", draft.seller), ("buyer", draft.buyer)):
        problem = None if party.vat_id is None else _vat_id_problem(party.vat_id)
        if problem is not None:
            findings.append(
                Finding(
                    type=FindingType.VAT_ID_INVALID,
                    severity=Severity.REVIEW,
                    message=Text(
                        "finding.vat_id_invalid",
                        party=Text(f"party.{role}"),
                        vat_id=party.vat_id,
                        problem=problem,
                    ),
                    fields=(f"{role}.vat_id",),
                    detail={"vat_id": party.vat_id, "role": role},
                )
            )
    return findings


def document_type(draft: InvoiceDraft, context: CheckContext) -> list[Finding]:
    """Anything but an ordinary invoice is looked at by a person (decision D9)."""
    if draft.type_code in (None, "380"):
        return []
    credit_note = draft.type_code == "381"
    return [
        Finding(
            type=FindingType.CREDIT_NOTE if credit_note else FindingType.UNUSUAL_DOCUMENT_TYPE,
            severity=Severity.REVIEW,
            message=(
                Text("finding.credit_note")
                if credit_note
                else Text("finding.unusual_document_type", type_code=draft.type_code)
            ),
            fields=("type_code",),
            detail={"type_code": draft.type_code},
        )
    ]


# --- duplicates -----------------------------------------------------------

_NOT_ALNUM = re.compile(r"[^a-z0-9]")
NEAR_NUMBER_DISTANCE = 2


def _key(text: str | None) -> str:
    return _NOT_ALNUM.sub("", (text or "").casefold())


def _same_seller(draft: InvoiceDraft, known: KnownInvoice) -> bool:
    if draft.seller.vat_id and known.seller_vat_id:
        return draft.seller.vat_id == known.seller_vat_id
    return bool(_key(draft.seller.name)) and _key(draft.seller.name) == _key(known.seller_name)


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b))
            )
        previous = current
    return previous[-1]


def duplicates(draft: InvoiceDraft, context: CheckContext) -> list[Finding]:
    """The same invoice again (same supplier and number); one that looks like
    it (same supplier, amount and date — a re-sent, re-scanned or retyped
    invoice); or one that bills a delivery another invoice already billed,
    whatever its number and date.

    A similar number alone does not make a near-duplicate: a supplier's
    consecutive invoices have similar numbers, and two equal part deliveries
    have the same amount as well. Only where a date is unknown does the
    number stand in for it.
    """
    number = _key(draft.number)
    findings = []
    reported = set()
    for known in context.known_invoices:
        if not _same_seller(draft, known):
            continue
        detail = {"document_id": known.document_id, "number": known.number}
        if number and number == _key(known.number):
            findings.append(
                Finding(
                    type=FindingType.DUPLICATE,
                    severity=Severity.REVIEW,
                    message=Text("finding.duplicate", number=known.number),
                    fields=("number",),
                    detail=detail,
                )
            )
            reported.add(known.document_id)
            continue
        same_amount = draft.gross_total is not None and draft.gross_total == known.gross_total
        similar_number = (
            bool(number)
            and bool(_key(known.number))
            and edit_distance(number, _key(known.number)) <= NEAR_NUMBER_DISTANCE
        )
        same_date = draft.issue_date is not None and draft.issue_date == known.issue_date
        undated = draft.issue_date is None or known.issue_date is None
        if same_amount and (same_date or (similar_number and undated)):
            reported.add(known.document_id)
            findings.append(
                Finding(
                    type=FindingType.NEAR_DUPLICATE,
                    severity=Severity.REVIEW,
                    message=Text(
                        "finding.near_duplicate_number"
                        if similar_number
                        else "finding.near_duplicate_date",
                        number=known.number,
                    ),
                    fields=("number", "gross_total"),
                    detail=detail,
                )
            )
    for known in context.delivery_billed_by:
        if known.document_id in reported:
            continue
        findings.append(
            Finding(
                type=FindingType.DELIVERY_ALREADY_BILLED,
                severity=Severity.REVIEW,
                message=Text(
                    "finding.delivery_already_billed",
                    delivery=context.delivery_id,
                    number=known.number,
                ),
                fields=("number",),
                detail={
                    "document_id": known.document_id,
                    "number": known.number,
                    "delivery": context.delivery_id,
                },
            )
        )
    return findings


# --- supplier and purchase order -------------------------------------------


def master_data(draft: InvoiceDraft, context: CheckContext) -> list[Finding]:
    """Says so when supplier, payment account and order were not checked. An
    ERP that should have answered and did not sends the invoice to a person:
    a changed bank account must not slip through because a service was down."""
    if context.master_data is MasterData.AVAILABLE:
        return []
    down = context.master_data is MasterData.UNAVAILABLE
    message = (
        Text("finding.master_data_unavailable", problem=context.master_data_problem)
        if down
        else Text("finding.master_data_none")
    )
    return [
        Finding(
            type=FindingType.MASTER_DATA_NOT_CHECKED,
            severity=Severity.REVIEW if down else Severity.INFO,
            message=message,
            detail={"reason": context.master_data.value},
        )
    ]


def supplier_and_iban(draft: InvoiceDraft, context: CheckContext) -> list[Finding]:
    """Payment details that differ from what is on file are the classic
    invoice fraud; they always go to a person."""
    if context.master_data is not MasterData.AVAILABLE:
        return []
    supplier = context.supplier
    if supplier is None:
        return [
            Finding(
                type=FindingType.UNKNOWN_SUPPLIER,
                severity=Severity.REVIEW,
                message=Text("finding.unknown_supplier"),
                fields=("seller.name", "seller.vat_id"),
                detail={"name": draft.seller.name, "vat_id": draft.seller.vat_id},
            )
        ]
    unknown = [iban for iban in draft.payment_ibans if iban not in supplier.ibans]
    if not unknown:
        return []
    return [
        Finding(
            type=FindingType.IBAN_CHANGED,
            severity=Severity.REVIEW,
            message=Text("finding.iban_changed", ibans=unknown, supplier=supplier.name),
            fields=("payment_ibans",),
            detail={"unknown": unknown, "on_file": list(supplier.ibans), "supplier": supplier.id},
        )
    ]


def _share_above(actual: Decimal, ordered: Decimal) -> Decimal | None:
    """How far actual is above ordered, as a share of ordered."""
    if ordered <= 0:
        return None
    return (actual - ordered) / ordered


def purchase_order(draft: InvoiceDraft, context: CheckContext) -> list[Finding]:
    if context.master_data is not MasterData.AVAILABLE:
        return []
    settings = context.settings
    order = context.purchase_order
    if order is None:
        if draft.po_ref is not None or context.purchase_order_problem is not None:
            problem = context.purchase_order_problem
            if draft.po_ref is not None:
                message = (
                    Text("finding.po_not_found_because", po_ref=draft.po_ref, problem=problem)
                    if problem
                    else Text("finding.po_not_found", po_ref=draft.po_ref)
                )
            else:
                message = (
                    Text("finding.no_po_because", problem=problem)
                    if problem
                    else Text("finding.missing_po")
                )
            return [
                Finding(
                    type=FindingType.PO_NOT_FOUND,
                    severity=Severity.REVIEW,
                    message=message,
                    fields=("po_ref",),
                    detail={"po_ref": draft.po_ref},
                )
            ]
        return [
            Finding(
                type=FindingType.MISSING_PO,
                severity=Severity.REVIEW if settings.po_required else Severity.INFO,
                message=Text("finding.missing_po"),
                fields=("po_ref",),
            )
        ]

    ordered = {_key(line.description): line for line in order.lines}
    findings = []
    if context.purchase_order_note is not None:
        findings.append(
            Finding(
                type=FindingType.PO_NOT_QUOTED,
                severity=Severity.INFO,
                message=context.purchase_order_note,
                fields=("po_ref",),
                detail={"po_ref": draft.po_ref, "order": order.number},
            )
        )
    for index, line in enumerate(draft.lines):
        match = ordered.get(_key(line.description))
        if match is None and len(order.lines) == len(draft.lines):
            match = order.lines[index]  # same number of positions: compare in order
        if match is None:
            continue
        for cell, kind, actual, agreed, tolerance in (
            ("unit_price", FindingType.PRICE_DEVIATION, line.unit_price, match.unit_price,
             settings.price_tolerance),
            ("quantity", FindingType.QUANTITY_DEVIATION, line.quantity, match.quantity,
             settings.quantity_tolerance),
        ):  # fmt: skip
            above = None if actual is None else _share_above(actual, agreed)
            if above is not None and above > tolerance:
                findings.append(
                    Finding(
                        type=kind,
                        severity=Severity.REVIEW,
                        message=Text(
                            f"finding.{kind.value}",
                            line=index + 1,
                            invoiced_amount=actual,
                            above_percent=above,
                            ordered_amount=agreed,
                            order=order.number,
                        ),
                        fields=(f"lines.{index}.{cell}",),
                        detail={
                            "invoiced": str(actual),
                            "ordered": str(agreed),
                            "above": str(round(above, 4)),
                            "order": order.number,
                        },
                    )
                )
    return findings


ALL_CHECKS: tuple[Check, ...] = (
    totals,
    vat_rates,
    vat_ids,
    document_type,
    duplicates,
    master_data,
    supplier_and_iban,
    purchase_order,
)


def run_checks(draft: InvoiceDraft, context: CheckContext) -> list[Finding]:
    return [finding for check in ALL_CHECKS for finding in check(draft, context)]
