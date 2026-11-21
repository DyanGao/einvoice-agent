"""Texts the server writes for people: findings, decision reasons, match
explanations, re-match triggers, delivery errors (v1.0 Stage 4).

A `Text` is a `str`, the English sentence, as before, so every place that
uses it as a string (logs, the eval, the stored message) keeps working. It
also carries a code and parameters: the review UI renders those in its own
language (ui/lib/messages.ts, keys "server.<code>"), and falls back to the
English sentence for a code it does not know. tests/test_texts.py checks
that every code here has a German and an English text in the UI, with the
same parameters.

Parameters are data: names, numbers, values from the invoice or another
system. A few kinds are recognised by name, so that the UI can format them
in its language:
- `…_amount`: a decimal, as a string;
- `…_percent`: a fraction (0.123), shown as a percentage;
- `fields`: a list of field paths ("lines.0.net"), shown by their labels;
- a parameter that is itself a Text, or a list of Texts (joined by "; ").
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from string import Formatter
from typing import Any

ENGLISH: dict[str, str] = {
    # Totals checks (app/checks/arithmetic.py)
    "check.line_net": "line {line}: quantity x unit price = net amount",
    "check.lines_total": "sum of the line amounts = line total",
    "check.vat_group": "VAT group {group}: rate x taxable amount = tax",
    "check.net_total": "sum of the taxable amounts = net total",
    "check.vat_total": "sum of the VAT groups = VAT total",
    "check.gross_total": "net total + VAT total = gross total",
    # Findings (app/checks/rules.py)
    "finding.totals_mismatch": "{check}: expected {expected_amount}, invoice says {actual_amount}",
    "finding.vat_rate_category": "VAT category {category} cannot carry a rate of {rate} %",
    "finding.vat_rate_not_german": "{rate} % is not a German VAT rate",
    "finding.vat_id_invalid": "{party} VAT ID {vat_id}: {problem}",
    "party.seller": "seller",
    "party.buyer": "buyer",
    "vat_id.german_shape": "a German VAT ID is DE followed by nine digits",
    "vat_id.german_check_digit": "the check digit of this German VAT ID is wrong",
    "vat_id.eu_shape": "this is not the shape of an EU VAT ID",
    "finding.credit_note": (
        "credit note: reduces an earlier invoice and is not booked automatically"
    ),
    "finding.unusual_document_type": "document type {type_code} is not an ordinary invoice",
    "finding.duplicate": "invoice {number} from this supplier is already on file",
    "finding.near_duplicate_number": (
        "looks like invoice {number} from this supplier, already on file: same amount and a "
        "similar number"
    ),
    "finding.near_duplicate_date": (
        "looks like invoice {number} from this supplier, already on file: same amount and date"
    ),
    "finding.delivery_already_billed": (
        "delivery {delivery} was already billed with invoice {number}, on file"
    ),
    "finding.master_data_unavailable": (
        "supplier, payment account and purchase order were not checked: the ERP did not answer "
        "({problem})"
    ),
    "finding.master_data_none": (
        "supplier, payment account and purchase order were not checked: no ERP is connected"
    ),
    "finding.unknown_supplier": "no supplier with this VAT ID or name is on file",
    "finding.iban_changed": (
        "payment account {ibans} is not on file for {supplier}; confirm it with the supplier "
        "through a known contact before paying"
    ),
    "finding.po_not_found": "purchase order {po_ref} named on the invoice does not exist",
    "finding.po_not_found_because": (
        "purchase order {po_ref} named on the invoice does not exist; {problem}"
    ),
    "finding.no_po_because": "the invoice names no purchase order; {problem}",
    "finding.missing_po": "the invoice names no purchase order",
    "finding.price_deviation": (
        "line {line}: price {invoiced_amount} is {above_percent} above the {ordered_amount} on "
        "order {order}"
    ),
    "finding.quantity_deviation": (
        "line {line}: quantity {invoiced_amount} is {above_percent} above the {ordered_amount} on "
        "order {order}"
    ),
    "order_note.slip": (
        "the invoice names {po_ref}; matched to order {order}, which differs from it by a slip; "
        "a delivery confirms it"
    ),
    "order_note.content": (
        "the invoice names {po_ref}; matched to order {order}, found by the delivered items"
    ),
    "order_note.content_unnamed": (
        "the invoice names no purchase order; matched to order {order}, found by the delivered "
        "items"
    ),
    # Decision reasons (app/decide/engine.py)
    "reason.missing_fields": "mandatory fields are missing or invalid: {fields}",
    "reason.assumed_value": "not on the document, assumed: {fields}",
    "reason.printed_not_read": "on the document but not read: {fields}",
    "reason.low_confidence": "read with less than {threshold_percent} confidence: {fields}",
    "reason.findings": "the checks found something a person has to look at: {details}",
    "reason.not_compared": (
        "not compared with the ERP: supplier, bank account and order are unconfirmed"
    ),
    "reason.order_by_content": (
        "the invoice quotes no usable order number; order {order} was found by what it bills, "
        "confirm it"
    ),
    "reason.not_delivered": "no delivery on order {order} matches what this invoice bills",
    "reason.not_read": "the document was not validated or read ({status})",
    "reason.reader_not_calibrated": (
        "read by {model}, a model the decision is not calibrated for: a person checks everything "
        "it reads"
    ),
    "reason.corrected": "a reviewer changed it; it waits for approval",
    # Match explanations (app/matching/matcher.py)
    "match.order_named": "order {order} is named on the invoice",
    "match.delivery_exact": "delivery {delivery} contains exactly the billed items",
    "match.no_single_delivery": (
        "no single delivery of that order contains exactly the billed items"
    ),
    "match.order_slip": (
        "the invoice names {po_ref}; order {order} differs by a slip and one of its deliveries "
        "contains exactly the billed items"
    ),
    "match.content_unknown_reference": (
        "the invoice names {po_ref}, which is no order of this supplier; delivery {delivery} of "
        "order {order} contains exactly the billed items"
    ),
    "match.content_no_reference": (
        "the invoice names no order; delivery {delivery} of order {order} contains exactly the "
        "billed items"
    ),
    "match.deliveries_ambiguous": "several deliveries fit equally well: {candidates}",
    "match.order_similar_unconfirmed": (
        "the invoice names {po_ref}; order {order} is similar, but no delivery confirms it"
    ),
    "match.no_order": "no order of this supplier fits the invoice",
    "match.no_total": "the invoice has no total to look for",
    "match.transfer_exact": "transfer {transfer} is exactly the invoice total",
    "match.transfer_shared": (
        "transfer {transfer} of {transfer_amount} equals this invoice's total plus other invoices "
        "of the supplier"
    ),
    "match.transfers_two": "transfers {first} and {second} add up to the invoice total",
    "match.no_transfer": "no transfer to this supplier pays this invoice",
    "match.transfer_twin": (
        "a transfer of the invoice total exists without an invoice number, but invoice {number} "
        "of this supplier has the same total"
    ),
    "match.transfers_ambiguous": "several transfers fit equally well: {transfers}",
    "match.payment_grounds": "{note} {grounds}",
    "grounds.exact_reference": "and quotes the invoice number",
    "grounds.similar_reference": "and quotes the invoice number with a slip",
    "grounds.content": "to the supplier's account, without an invoice number",
    "match.no_supplier": "the supplier is not on file, so nothing can be matched",
    "match.payments_again": (
        "payments matched again; order {order} and delivery {delivery} stay as approved"
    ),
    # What set off a re-match (app/rematch.py)
    "trigger.new_invoice": "new invoice {number} of the supplier",
    "trigger.new_transfer": "new bank transaction {transfers}",
    "trigger.new_transfers": "new bank transactions {transfers}",
    "trigger.new_transfers_more": "new bank transactions {transfers} and {more} more",
    # Why a delivery did not happen (app/connectors, app/delivery.py)
    "delivery.supplier_unknown": "the supplier is not known in the ERP",
    "delivery.not_an_invoice": (
        "document type {type_code} is not an ordinary invoice; credit notes and other types are "
        "booked by hand"
    ),
    "delivery.erp_unreachable": "ERP not reachable: {detail}",
    "delivery.erp_failed": "ERP failed with {status}: {detail}",
    "delivery.erp_refused": "ERP refused the booking ({status}): {detail}",
    "delivery.csv_encoding": (
        "invoice {number}: {characters} cannot be written in Windows-1252, the encoding of the "
        "booking list"
    ),
    "delivery.csv_not_written": "booking list {file} could not be written: {detail}",
    "delivery.csv_conflict": "{file} line {line} already holds this invoice with other values",
    "delivery.webhook_unreachable": "webhook receiver not reachable: {detail}",
    "delivery.webhook_failed": "webhook receiver failed with {status}",
    "delivery.webhook_redirect": (
        "webhook receiver answered {status} (redirect to {location}); redirects are not followed"
    ),
    "delivery.webhook_refused": "webhook receiver refused the booking with {status}",
    "delivery.form_changed": "the ERP's booking form has changed: {detail}; nothing was booked",
    "delivery.form_blocked": (
        "the browser was sent to {url}, which is not on the allowlist; blocked, nothing was "
        "booked there"
    ),
    "delivery.form_unusable": "ERP screens not usable: {detail}",
    "delivery.form_status": "ERP answered {status} for {url}",
    "delivery.form_several": "the ERP holds {count} bookings for reference {reference}: {ids}",
    "delivery.form_differs": "booking {booking} in the ERP: {refuse}: {fields_differing}",
    # Why the API refused a request (app/api/main.py)
    "api.reviewer_name_required": "X-Reviewer header with the reviewer's name is required",
    "api.csrf_header": "a change made with the session cookie needs {header}",
    "api.open_mode_localhost": (
        "sign-in is off (AUTH=off): only requests to localhost are answered"
    ),
    "api.sign_in": "sign in, or send an API token",
    "api.needs_role": "needs the {role} role",
    "role.viewer": "viewer",
    "role.reviewer": "reviewer",
    "role.admin": "admin",
    "api.document_not_found": "document not found",
    "api.sign_in_off": "sign-in is off (AUTH=off)",
    "api.no_provider": "no identity provider is configured (OIDC_ISSUER)",
    "api.too_many_sign_ins": "too many failed sign-ins; try again in {minutes} minute(s)",
    "api.wrong_password": "wrong email or password",
    "api.provider_unreachable": "the identity provider cannot be reached",
    "api.not_signed_in": "not signed in",
    "api.token_cannot_switch": "an API token acts in the tenant it was made for",
    "api.no_such_tenant": "no such tenant among yours",
    "api.members_cli": "sign-in is off (AUTH=off): manage members with the einvoice CLI",
    "api.member_already": "{email} is a member already",
    "api.member_refused": "{detail}",
    "api.operator_settings": (
        "{settings}: set by the installation's operator (einvoice tenant-set)"
    ),
    "api.invalid_settings": "settings that cannot work: {detail}",
    "api.empty_file": "empty file",
    "api.file_too_large": "file larger than {bytes} bytes",
    "api.not_queued": "document stored but not queued ({detail}); run `einvoice requeue`",
    "api.no_checks": "no checks are configured for this installation",
    "api.nothing_to_check": "nothing was read from this document, so nothing to check",
    # Why a review action was refused (app/db/review.py)
    "review.already_approved": "document is already approved",
    "review.rejected": "document was rejected",
    "review.not_processed": "document has not been processed yet",
    "review.no_draft": "document has no draft to approve",
    "review.not_editable": "field cannot be edited: {path}",
    "review.unknown_field": "unknown field: {path}",
    "review.invalid_value": "invalid value: {problems}",
    "review.fields_missing": "cannot approve, fields missing or invalid: {fields}",
    "review.unknown_reason": "unknown reason {reason}; one of: {allowed}",
    "review.note_needed": "a rejection for another reason needs a note saying which",
    "review.not_rejected": "document is not rejected",
    "review.reopen_note": "reopening needs a note saying why",
    # The note of a copy rejected with "reject all duplicates".
    "review.copy_of": "copy of invoice {number}, document {document}",
    "review.copy_of_unnumbered": "copy of invoice (no number), document {document}",
    # Changes to a tenant's members that are refused (app/auth)
    "members.not_member": "not a member of this tenant",
    "members.own_admin": "you cannot take away your own admin role",
    "members.last_admin": "the tenant would be left without an admin",
    "members.self_remove": "you cannot remove yourself",
}

_FIELDS = Formatter()


def _english(value: Any, name: str) -> str:
    """A parameter as it goes into the English sentence."""
    if isinstance(value, Text):
        return str(value)
    if isinstance(value, Sequence) and not isinstance(value, str):
        if all(isinstance(item, Text) for item in value):
            return "; ".join(map(str, value))
        return ", ".join(map(str, value))
    if name.endswith("_percent"):
        return f"{Decimal(str(value)) * 100:.1f}%"
    return str(value)


def _plain(value: Any) -> Any:
    """A parameter as it is stored and sent to the UI."""
    if isinstance(value, Text):
        return value.said()
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [_plain(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


class Text(str):
    """An English sentence that knows its code and parameters."""

    code: str
    params: Mapping[str, Any]

    def __new__(cls, code: str, **params: Any) -> "Text":
        template = ENGLISH[code]
        names = {name for _, name, _, _ in _FIELDS.parse(template) if name}
        if names != set(params):
            raise ValueError(f"{code}: parameters {sorted(params)}, the text has {sorted(names)}")
        english = template.format(**{name: _english(value, name) for name, value in params.items()})
        text = super().__new__(cls, english)
        text.code = code
        text.params = params
        return text

    def said(self) -> dict[str, Any]:
        """For storage and the UI: the code, plain parameters, and the English."""
        return {
            "code": self.code,
            "params": {name: _plain(value) for name, value in self.params.items()},
            "text": str(self),
        }


def said(value: str | None) -> dict[str, Any] | None:
    """The code of a text that has one; None for plain strings (and old rows)."""
    return value.said() if isinstance(value, Text) else None


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in _FIELDS.parse(template) if name}
