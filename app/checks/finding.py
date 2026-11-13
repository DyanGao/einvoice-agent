"""What a check reports: something about an invoice a person should know.

ARCHITECTURE.md calls this an Exception; the code says Finding because
`Exception` already means something else in Python.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FindingType(StrEnum):
    TOTALS_MISMATCH = "totals_mismatch"
    VAT_RATE_IMPLAUSIBLE = "vat_rate_implausible"
    VAT_ID_INVALID = "vat_id_invalid"
    CREDIT_NOTE = "credit_note"
    UNUSUAL_DOCUMENT_TYPE = "unusual_document_type"
    DUPLICATE = "duplicate"
    NEAR_DUPLICATE = "near_duplicate"
    UNKNOWN_SUPPLIER = "unknown_supplier"
    IBAN_CHANGED = "iban_changed"
    MISSING_PO = "missing_po"
    PO_NOT_FOUND = "po_not_found"
    PRICE_DEVIATION = "price_deviation"
    QUANTITY_DEVIATION = "quantity_deviation"
    # The supplier, payment account and order could not be compared with the
    # ERP's data, so those checks did not run.
    MASTER_DATA_NOT_CHECKED = "master_data_not_checked"


class Severity(StrEnum):
    # A person has to look before this invoice is booked or paid.
    REVIEW = "review"
    # Worth showing, but no reason on its own to stop the invoice.
    INFO = "info"


@dataclass(frozen=True)
class Finding:
    type: FindingType
    severity: Severity
    message: str
    # Dotted paths of the invoice fields involved, as the review UI uses them.
    fields: tuple[str, ...] = ()
    # Values behind the message, for the audit trail and the eval.
    detail: dict[str, Any] = field(default_factory=dict)
