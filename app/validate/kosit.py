"""Client for the KoSIT validator sidecar (daemon mode).

The daemon takes an XML document via POST / and answers with a report in the
VARL format: HTTP 200 if it recommends accepting the document, 406 if it
recommends rejecting it. Anything else means no verdict exists — that is an
error, never "valid".
"""

import httpx
from lxml import etree

from app import safe_xml
from app.validate.result import Severity, ValidationResult, Violation

REP = "http://www.xoev.de/de/validator/varl/1"
SCN = "http://www.xoev.de/de/validator/framework/1/scenarios"
NS = {"rep": REP, "s": SCN}

VERDICT_STATUS = {200: True, 406: False}


class ValidatorError(RuntimeError):
    """The validator could not be reached or did not return a usable verdict."""


def parse_report(report_xml: bytes) -> ValidationResult:
    try:
        root = safe_xml.parse(report_xml)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ValidatorError(f"validator returned an unreadable report: {exc}") from exc
    if root.tag != f"{{{REP}}}report":
        raise ValidatorError(f"unexpected validator response root: {root.tag}")

    accepted = root.find("rep:assessment/rep:accept", NS) is not None
    rejected = root.find("rep:assessment/rep:reject", NS) is not None
    if accepted == rejected:
        raise ValidatorError("validator report carries no unambiguous accept/reject assessment")

    violations = tuple(
        Violation(
            rule_id=message.get("code") or message.get("id") or "unknown",
            severity=Severity(message.get("level")),
            message=(message.text or "").strip(),
            location=message.get("xpathLocation") or _line_location(message),
        )
        for message in root.iterfind(".//rep:validationStepResult/rep:message", NS)
    )
    return ValidationResult(
        valid=accepted,
        scenario=root.findtext("rep:scenarioMatched/s:scenario/s:name", namespaces=NS),
        violations=violations,
    )


def _line_location(message: etree._Element) -> str | None:
    line = message.get("lineNumber")
    if line is None:
        return None
    column = message.get("columnNumber")
    return f"line {line}" + (f", column {column}" if column else "")


class KositValidator:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def validate(self, xml: bytes) -> ValidationResult:
        try:
            response = self._client.post(
                "/", content=xml, headers={"Content-Type": "application/xml"}
            )
        except httpx.HTTPError as exc:
            raise ValidatorError(f"validator not reachable: {exc}") from exc
        if response.status_code not in VERDICT_STATUS:
            raise ValidatorError(
                f"validator answered HTTP {response.status_code}: {response.text[:200]}"
            )
        result = parse_report(response.content)
        if result.valid != VERDICT_STATUS[response.status_code]:
            raise ValidatorError(
                f"validator HTTP status {response.status_code} contradicts its report"
            )
        return result
