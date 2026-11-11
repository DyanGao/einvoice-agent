from pathlib import Path

import httpx
import pytest

from app.validate import KositValidator, Severity, ValidatorError, parse_report

REPORTS = Path(__file__).parent / "reports"


def report(name: str) -> bytes:
    return (REPORTS / name).read_bytes()


def validator_answering(status: int, body: bytes) -> KositValidator:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, content=body))
    return KositValidator(httpx.Client(transport=transport, base_url="http://validator"))


def test_accepted_document_is_valid():
    assert parse_report(report("accept.xml")).valid


def test_accepted_document_reports_matched_scenario():
    assert parse_report(report("accept.xml")).scenario == "EN16931 XRechnung (UBL Invoice)"


def test_information_messages_do_not_count_as_errors():
    assert parse_report(report("accept.xml")).errors == ()


def test_rejected_document_is_invalid():
    assert not parse_report(report("reject_business_rule.xml")).valid


def test_business_rule_violation_carries_rule_id():
    result = parse_report(report("reject_business_rule.xml"))
    assert [v.rule_id for v in result.errors] == ["BR-DE-15"]


def test_business_rule_violation_carries_xpath_location():
    violation = parse_report(report("reject_business_rule.xml")).errors[0]
    assert violation.location.endswith("Invoice[1]")


def test_all_severities_are_kept():
    result = parse_report(report("reject_business_rule.xml"))
    assert [v.severity for v in result.violations] == [Severity.ERROR, Severity.INFORMATION]


def test_schema_violation_location_falls_back_to_line_and_column():
    violation = parse_report(report("reject_schema.xml")).errors[0]
    assert violation.location == "line 5, column 20"


def test_unrecognised_document_type_is_invalid_without_scenario():
    result = parse_report(report("reject_no_scenario.xml"))
    assert (result.valid, result.scenario) == (False, None)


def test_report_without_assessment_is_an_error():
    body = b'<rep:report xmlns:rep="http://www.xoev.de/de/validator/varl/1"/>'
    with pytest.raises(ValidatorError, match="accept/reject"):
        parse_report(body)


def test_non_report_response_is_an_error():
    with pytest.raises(ValidatorError, match="unexpected"):
        parse_report(b"<html/>")


def test_client_returns_valid_for_http_200_accept():
    assert validator_answering(200, report("accept.xml")).validate(b"<x/>").valid


def test_client_returns_invalid_for_http_406_reject():
    result = validator_answering(406, report("reject_business_rule.xml")).validate(b"<x/>")
    assert not result.valid


def test_server_error_is_never_reported_as_valid():
    with pytest.raises(ValidatorError, match="HTTP 500"):
        validator_answering(500, b"boom").validate(b"<x/>")


def test_status_contradicting_report_is_an_error():
    with pytest.raises(ValidatorError, match="contradicts"):
        validator_answering(200, report("reject_business_rule.xml")).validate(b"<x/>")


def test_unreachable_validator_is_an_error():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://validator")
    with pytest.raises(ValidatorError, match="not reachable"):
        KositValidator(client).validate(b"<x/>")


def test_document_is_sent_unchanged_as_xml():
    seen = {}

    def capture(request: httpx.Request) -> httpx.Response:
        seen["body"], seen["type"] = request.content, request.headers["content-type"]
        return httpx.Response(200, content=report("accept.xml"))

    client = httpx.Client(transport=httpx.MockTransport(capture), base_url="http://validator")
    KositValidator(client).validate(b"<Invoice>\xc3\xa4</Invoice>")
    assert seen == {"body": b"<Invoice>\xc3\xa4</Invoice>", "type": "application/xml"}
