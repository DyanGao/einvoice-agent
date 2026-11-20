"""The decision engine: book an invoice without a person, or send it to one.

An invoice is booked automatically only when nothing speaks against it. Every
reason that does is collected, not only the first, so the reviewer sees all of
them. Two kinds of reasons:

- reading: a mandatory field is missing, a value was assumed instead of read,
  a value printed in a standard format was not read, or a field was read with
  less confidence than the threshold;
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
from app.texts import Text

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
    PRINTED_NOT_READ = "printed_not_read"
    LOW_CONFIDENCE = "low_confidence"
    FINDINGS = "findings"
    NOT_COMPARED = "not_compared"
    ORDER_BY_CONTENT = "order_by_content"
    NOT_DELIVERED = "not_delivered"
    CORRECTED = "corrected"
    READER_NOT_CALIBRATED = "reader_not_calibrated"


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
    # reference on the invoice, is good enough to book on. No, by decision
    # (2026-11-14): the matcher made no wrong link in the world, but a link
    # nobody quoted is confirmed by a person before money follows it.
    auto_on_order_found_by_content: bool = False
    version: str = "unversioned"
    # The model reading PDFs and scans, when the threshold was not calibrated
    # on its answers: everything it reads goes to a person. A local model's
    # misreads fall where the hosted model's confidence looks for none (4 of
    # 9 booked without a person were misread, docs/eval-report.md).
    uncalibrated_reader: str | None = None

    @classmethod
    def load(cls, path: Path = THRESHOLDS_FILE) -> "DecisionSettings":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(min_field_confidence=data["min_field_confidence"], version=data["version"])


def reading_causes(draft: InvoiceDraft, settings: DecisionSettings) -> list[Reason]:
    """Whether what was read can be trusted without a person looking."""
    reasons = []
    missing = missing_for_approval(draft)
    if missing:
        reasons.append(
            Reason(
                Cause.MISSING_FIELDS,
                Text("reason.missing_fields", fields=list(missing)),
                fields=tuple(missing),
            )
        )
    assumed = tuple(path for path in draft.derived_fields if path in ASSUMED_FIELDS)
    if assumed:
        reasons.append(
            Reason(
                Cause.ASSUMED_VALUE,
                Text("reason.assumed_value", fields=list(assumed)),
                fields=assumed,
            )
        )
    if draft.unread_fields:
        reasons.append(
            Reason(
                Cause.PRINTED_NOT_READ,
                Text("reason.printed_not_read", fields=list(draft.unread_fields)),
                fields=tuple(draft.unread_fields),
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
                Text(
                    "reason.low_confidence",
                    threshold_percent=settings.min_field_confidence,
                    fields=list(unsure),
                ),
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
                Text("reason.findings", details=[finding.message for finding in serious]),
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
                    Text("reason.not_compared"),
                )
            )
        return reasons
    if match.po_number is not None:
        if match.po_basis is Basis.CONTENT and not settings.auto_on_order_found_by_content:
            reasons.append(
                Reason(
                    Cause.ORDER_BY_CONTENT,
                    Text("reason.order_by_content", order=match.po_number),
                    fields=("po_ref",),
                )
            )
        if match.delivery_id is None:
            reasons.append(
                Reason(
                    Cause.NOT_DELIVERED,
                    Text("reason.not_delivered", order=match.po_number),
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
        reasons = [Reason(Cause.NOT_READ, Text("reason.not_read", status=status.value))]
    else:
        reasons = reading_causes(draft, settings) + business_causes(findings, match, settings)
    if status is DocumentStatus.EXTRACTED and settings.uncalibrated_reader is not None:
        reasons.append(
            Reason(
                Cause.READER_NOT_CALIBRATED,
                Text("reason.reader_not_calibrated", model=settings.uncalibrated_reader),
            )
        )
    if corrected:
        reasons.append(Reason(Cause.CORRECTED, Text("reason.corrected")))
    return Decision(
        route=Route.REVIEW if reasons else Route.AUTO,
        reasons=tuple(reasons),
        version=settings.version,
    )
