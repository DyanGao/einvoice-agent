import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from tests.conftest import REJECT, StubValidator
from tests.support import PDF, PDF_DOCUMENT, XML, BrokenQueue, inline_queue, settings_for

REVIEWER = {"X-Reviewer": "anna"}


@pytest.fixture
def settings(tmp_path):
    return settings_for(tmp_path, max_upload_bytes=2_000_000)


@pytest.fixture
def client(session_factory, settings):
    return TestClient(create_app(session_factory, inline_queue(session_factory), settings))


def upload(client, data=PDF, name="rechnung.pdf"):
    return client.post("/documents", files={"file": (name, data)})


def uploaded(client, data=PDF, name="rechnung.pdf") -> str:
    return upload(client, data, name).json()["id"]


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_the_bare_address_leads_to_the_api_page_instead_of_not_found(client):
    response = client.get("/", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (307, "/docs")


def test_upload_is_accepted_and_returns_an_id(client):
    response = upload(client)
    assert (response.status_code, response.json()["duplicate"]) == (202, False)


def test_uploaded_pdf_is_extracted_and_waits_for_review(client):
    detail = client.get(f"/documents/{uploaded(client)}").json()
    assert (detail["status"], detail["review_state"], detail["fields"]["number"]) == (
        "extracted",
        "pending",
        PDF_DOCUMENT.invoice.number,
    )


def test_uploaded_valid_xrechnung_without_checks_waits_for_review(client):
    detail = client.get(f"/documents/{uploaded(client, XML, 'r.xml')}").json()
    assert (detail["status"], detail["review_state"], detail["validation"]["valid"]) == (
        "valid",
        "pending",
        True,
    )


def test_rejected_xrechnung_shows_the_violated_rule(session_factory, settings):
    queue = inline_queue(session_factory, validator=StubValidator(REJECT))
    client = TestClient(create_app(session_factory, queue, settings))
    detail = client.get(f"/documents/{uploaded(client, XML, 'r.xml')}").json()
    assert detail["validation"]["violations"][0]["rule_id"] == "BR-DE-15"


def test_uploading_the_same_file_again_returns_the_existing_document(client):
    first = upload(client)
    second = upload(client)
    assert (second.status_code, second.json()["id"], second.json()["duplicate"]) == (
        200,
        first.json()["id"],
        True,
    )


def test_empty_upload_is_rejected(client):
    assert upload(client, b"").status_code == 400


def test_oversized_upload_is_rejected(client):
    assert upload(client, b"x" * 2_000_001).status_code == 413


def test_upload_without_a_file_is_rejected(client):
    assert client.post("/documents").status_code == 422


def test_queue_outage_reports_that_the_document_is_stored_but_not_queued(session_factory, settings):
    client = TestClient(create_app(session_factory, BrokenQueue(), settings))
    response = upload(client)
    assert (response.status_code, "einvoice requeue" in response.json()["detail"]) == (503, True)
    assert len(client.get("/documents").json()["documents"]) == 1


def test_unknown_document_is_not_found(client):
    assert client.get("/documents/nope").status_code == 404


def test_list_shows_summaries_newest_first(client):
    uploaded(client, XML, "r.xml")
    newest = uploaded(client)
    documents = client.get("/documents").json()["documents"]
    assert (len(documents), documents[0]["id"], documents[0]["number"]) == (
        2,
        newest,
        PDF_DOCUMENT.invoice.number,
    )


def test_list_can_be_filtered_to_the_review_queue(client):
    approved = uploaded(client, XML, "r.xml")
    assert client.post(f"/documents/{approved}/approve", headers=REVIEWER).status_code == 200
    pending = uploaded(client)
    documents = client.get("/documents", params={"review_state": "pending"}).json()["documents"]
    assert [d["id"] for d in documents] == [pending]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 500}, {"review_state": "bogus"}])
def test_invalid_list_parameters_are_rejected(client, params):
    assert client.get("/documents", params=params).status_code == 422


def test_pdf_content_is_served_for_inline_display(client):
    response = client.get(f"/documents/{uploaded(client)}/content")
    assert (response.content, response.headers["content-type"]) == (PDF, "application/pdf")
    assert response.headers["content-disposition"] == "inline"


def test_xml_content_is_download_only_so_a_browser_never_renders_it(client):
    response = client.get(f"/documents/{uploaded(client, XML, 'r.xml')}/content")
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_html_upload_is_never_served_as_html(client):
    document_id = uploaded(client, b"<html><script>alert(1)</script></html>", "x.html")
    response = client.get(f"/documents/{document_id}/content")
    assert response.headers["content-type"] == "application/octet-stream"


def test_detail_says_a_pdf_can_be_displayed(client):
    assert client.get(f"/documents/{uploaded(client)}").json()["content_type"] == "application/pdf"


def test_detail_says_xml_cannot_be_displayed(client):
    detail = client.get(f"/documents/{uploaded(client, XML, 'r.xml')}").json()
    assert detail["content_type"] is None


def test_correction_needs_a_reviewer_name(client):
    response = client.patch(
        f"/documents/{uploaded(client)}/fields", json={"changes": {"number": "X"}}
    )
    assert response.status_code == 400


def test_correction_is_applied_and_shown_in_the_audit_trail(client):
    document_id = uploaded(client)
    response = client.patch(
        f"/documents/{document_id}/fields",
        json={"changes": {"number": "RE-KORRIGIERT"}},
        headers=REVIEWER,
    )
    body = response.json()
    assert (response.status_code, body["changed"], body["fields"]["number"]) == (
        200,
        ["number"],
        "RE-KORRIGIERT",
    )
    assert body["original"]["number"] == PDF_DOCUMENT.invoice.number
    assert body["audit"][-1] == {
        **body["audit"][-1],
        "event": "field_corrected",
        "actor": "anna",
        "detail": {
            "field": "number",
            "old": PDF_DOCUMENT.invoice.number,
            "new": "RE-KORRIGIERT",
        },
    }


@pytest.mark.parametrize(
    "changes",
    [{"no_such_field": "x"}, {"gross_total": "viel"}, {"gross_total": 12.5}],
    ids=["unknown-field", "not-a-number", "float"],
)
def test_bad_correction_is_rejected_with_a_reason(client, changes):
    response = client.patch(
        f"/documents/{uploaded(client)}/fields", json={"changes": changes}, headers=REVIEWER
    )
    assert (response.status_code, bool(response.json()["detail"])) == (422, True)


def test_detail_lists_what_is_missing_for_approval(client):
    document_id = uploaded(client)
    body = client.patch(
        f"/documents/{document_id}/fields",
        json={"changes": {"gross_total": None}},
        headers=REVIEWER,
    ).json()
    assert body["missing"] == ["gross_total"]


def test_incomplete_document_cannot_be_approved(client):
    document_id = uploaded(client)
    client.patch(
        f"/documents/{document_id}/fields",
        json={"changes": {"gross_total": None}},
        headers=REVIEWER,
    )
    response = client.post(f"/documents/{document_id}/approve", headers=REVIEWER)
    assert (response.status_code, "gross_total" in response.json()["detail"]) == (422, True)


def test_approval_needs_a_reviewer_name(client):
    assert client.post(f"/documents/{uploaded(client)}/approve").status_code == 400


def test_approval_marks_the_document_approved_by_the_reviewer(client):
    response = client.post(f"/documents/{uploaded(client)}/approve", headers=REVIEWER)
    body = response.json()
    assert (response.status_code, body["review_state"], body["audit"][-1]["actor"]) == (
        200,
        "approved",
        "anna",
    )


def test_approved_document_can_no_longer_be_changed(client):
    document_id = uploaded(client)
    client.post(f"/documents/{document_id}/approve", headers=REVIEWER)
    patch = client.patch(
        f"/documents/{document_id}/fields", json={"changes": {"number": "X"}}, headers=REVIEWER
    )
    again = client.post(f"/documents/{document_id}/approve", headers=REVIEWER)
    assert (patch.status_code, again.status_code) == (409, 409)


def test_approval_writes_a_feedback_case_with_the_corrections(client, settings):
    document_id = uploaded(client)
    client.patch(
        f"/documents/{document_id}/fields",
        json={"changes": {"number": "RE-KORRIGIERT"}},
        headers=REVIEWER,
    )
    client.post(f"/documents/{document_id}/approve", headers=REVIEWER)
    (case_file,) = settings.feedback_dir.iterdir()
    case = json.loads(case_file.read_text())
    assert (case["original"]["number"], case["approved"]["number"], case["corrections"]) == (
        PDF_DOCUMENT.invoice.number,
        "RE-KORRIGIERT",
        [{"field": "number", "old": PDF_DOCUMENT.invoice.number, "new": "RE-KORRIGIERT"}],
    )


def test_feedback_cases_go_to_a_folder_git_ignores_by_default():
    from app.settings import Settings

    assert str(Settings.from_env().feedback_dir).startswith("data/private")


def test_review_ui_origin_may_call_the_api(client, settings):
    response = client.get("/health", headers={"Origin": settings.ui_origin})
    assert response.headers["access-control-allow-origin"] == settings.ui_origin


def test_other_origins_get_no_cors_permission(client):
    response = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers
