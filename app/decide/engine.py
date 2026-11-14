"""The decision engine: book an invoice without a person, or send it to one.

An invoice is booked automatically only when nothing speaks against it. Every
reason that does is collected, not only the first, so the reviewer sees all of
them. Two kinds of reasons:

- reading: a mandatory field is missing, a value was assumed instead of read,
  or a field was read with less confidence than the threshold;
- business: a check needs a person, the invoice could not be compared with
  the ERP, its order was only found by what it bills, nothing of the order
  has been delivered, or a reviewer changed it.

The confidence threshold is the one number chosen from data
(eval/sweep.py writes it); the other rules are policy and hold at any
confidence: a changed bank account goes to a person even if every field was
read with certainty.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.checks.finding import Finding, FindingType, Severity
from app.db.review import missing_for_approval
from app.extract.confidence import ASSUMED_FIELDS
from app.extract.draft import InvoiceDraft
from app.matching import Basis, Match
from app.pipeline import DocumentStatus

THRESHOLDS_FILE = Path(__file__).with_name("thresholds.json")

# Only these have an invoice worth deciding on; every other status is a
# document that could not be validated or read.
_DECIDABLE = frozenset({DocumentStatus.VALID, DocumentStatus.EXTRACTED})


class Route(StrEnum):
    AUTO = "auto"
    REVIEW = "review"


class Cause(StrEnum):
    NOT_READ = "not_read"
    MISSING_FIELDS = "missing_fields"
    ASSUMED_VALUE = "assumed_value"
    LOW_CONFIDENCE = "low_confidence"
    FINDINGS = "findings"
    NOT_COMPARED = "not_compared"
    ORDER_BY_CONTENT = "order_by_content"
    NOT_DELIVERED = "not_delivered"
    CORRECTED = "corrected"


@dataclass(frozen=True)
class Reason:
    cause: Cause
    message: str
    # Dotted paths of the fields concerned, as the review UI uses them.
    fields: tuple[str, ...] = ()
    # Types of the findings concerned.
    findings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    route: Route
    reasons: tuple[Reason, ...]
    # Which thresholds made it, for the audit trail.
    version: str


@dataclass(frozen=True)
class DecisionSettings:
    # A field read with less confidence than this sends the invoice to a person.
    min_field_confidence: float
    # Whether an order found by the lines and amount alone, without a
    # reference on the invoice, is good enough to book on.
    auto_on_order_found_by_content: bool = False
    version: str = "unversioned"

    @classmethod
    def load(cls, path: Path = THRESHOLDS_FILE) -> "DecisionSettings":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(min_field_confidence=data["min_field_confidence"], version=data["version"])


def _listed(paths: Iterable[str]) -> str:
    return ", ".join(paths)


def reading_causes(draft: InvoiceDraft, settings: DecisionSettings) -> list[Reason]:
    """Whether what was read can be trusted without a person looking."""
    reasons = []
    missing = missing_for_approval(draft)
    if missing:
        reasons.append(
            Reason(
                Cause.MISSING_FIELDS,
                f"mandatory fields are missing or invalid: {_listed(missing)}",
                fields=tuple(missing),
            )
        )
    assumed = tuple(path for path in draft.derived_fields if path in ASSUMED_FIELDS)
    if assumed:
        reasons.append(
            Reason(
                Cause.ASSUMED_VALUE,
                f"not on the document, assumed: {_listed(assumed)}",
                fields=assumed,
            )
        )
    unsure = tuple(
        path
        for path, confidence in sorted(draft.field_confidence.items())
        if path not in assumed and confidence < settings.min_field_confidence
    )
    if unsure:
        reasons.append(
            Reason(
                Cause.LOW_CONFIDENCE,
                f"read with less than {settings.min_field_confidence:.0%} confidence: "
                f"{_listed(unsure)}",
                fields=unsure,
            )
        )
    return reasons


def business_causes(
    findings: Iterable[Finding], match: Match | None, settings: DecisionSettings
) -> list[Reason]:
    """Whether the invoice may be booked, given what the checks and matching found."""
    findings = list(findings)
    reasons = []
    serious = [finding for finding in findings if finding.severity is Severity.REVIEW]
    if serious:
        reasons.append(
            Reason(
                Cause.FINDINGS,
                "the checks found something a person has to look at: "
                + "; ".join(finding.message for finding in serious),
                fields=tuple(dict.fromkeys(path for f in serious for path in f.fields)),
                findings=tuple(dict.fromkeys(finding.type.value for finding in serious)),
            )
        )
    not_compared = any(f.type is FindingType.MASTER_DATA_NOT_CHECKED for f in findings)
    if match is None or not_compared:
        if not any(f.type is FindingType.MASTER_DATA_NOT_CHECKED for f in serious):
            reasons.append(
                Reason(
                    Cause.NOT_COMPARED,
                    "not compared with the ERP: supplier, bank account and order are unconfirmed",
                )
            )
        return reasons
    if match.po_number is not None:
        if match.po_basis is Basis.CONTENT and not settings.auto_on_order_found_by_content:
            reasons.append(
                Reason(
                    Cause.ORDER_BY_CONTENT,
                    f"the invoice quotes no usable order number; order {match.po_number} was "
                    "found by what it bills, confirm it",
                    fields=("po_ref",),
                )
            )
        if match.delivery_id is None:
            reasons.append(
                Reason(
                    Cause.NOT_DELIVERED,
                    f"no delivery on order {match.po_number} matches what this invoice bills",
                )
            )
    return reasons


def decide(
    draft: InvoiceDraft,
    *,
    status: DocumentStatus,
    findings: Iterable[Finding],
    match: Match | None,
    corrected: bool,
    settings: DecisionSettings,
) -> Decision:
    if status not in _DECIDABLE:
        reasons = [
            Reason(Cause.NOT_READ, f"the document was not validated or read ({status.value})")
        ]
    else:
        reasons = reading_causes(draft, settings) + business_causes(findings, match, settings)
    if corrected:
        reasons.append(Reason(Cause.CORRECTED, "a reviewer changed it; it waits for approval"))
    return Decision(
        route=Route.REVIEW if reasons else Route.AUTO,
        reasons=tuple(reasons),
        version=settings.version,
    )
