"""The local model client, against a fake Ollama server."""

import base64
import json

import httpx
import pytest

from app.extract.llm import ExtractionError, LlmRequest
from app.extract.ollama import (
    DATE_PATTERN,
    NUMBER_PATTERN,
    OllamaLlm,
    is_local,
    page_images,
    with_formats,
)
from app.extract.wire import WireInvoice
from tests.support import PDF

JPEG = b"\xff\xd8\xff\xe0fake-scan"


def request(document=JPEG, media_type="image/jpeg") -> LlmRequest:
    return LlmRequest(
        model="ollama/qwen2.5vl:7b",
        system="system text",
        prompt="Transcribe this invoice.",
        prompt_version="v",
        document=document,
        media_type=media_type,
        schema={"type": "object", "properties": {"number": {"type": "string"}}},
    )


def server(answer: dict, status: int = 200, seen: list | None = None) -> httpx.Client:
    def handle(incoming: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(incoming.content))
        return httpx.Response(status, json=answer)

    return httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(handle))


ANSWER = {
    "message": {"content": json.dumps({"number": "RE-1"})},
    "done_reason": "stop",
    "prompt_eval_count": 1200,
    "eval_count": 80,
}


def test_only_models_named_ollama_are_local():
    assert is_local("ollama/qwen2.5vl:7b")
    assert not is_local("claude-opus-5-5")


def test_request_carries_schema_image_and_instructions_and_no_randomness():
    seen = []
    OllamaLlm(server(ANSWER, seen=seen)).complete(request())
    (body,) = seen
    assert body["model"] == "qwen2.5vl:7b"
    assert body["format"] == request().schema
    assert body["options"]["temperature"] == 0
    system, user = body["messages"]
    assert (system["role"], system["content"]) == ("system", "system text")
    assert user["role"] == "user"
    assert base64.b64decode(user["images"][0]) == JPEG


def test_answer_becomes_a_response_with_token_counts():
    response = OllamaLlm(server(ANSWER)).complete(request())
    assert (response.output, response.input_tokens, response.output_tokens) == (
        {"number": "RE-1"},
        1200,
        80,
    )
    assert response.model == "ollama/qwen2.5vl:7b"


def test_pdf_is_sent_as_page_images():
    images = page_images(PDF, "application/pdf")
    assert images
    assert all(image.startswith(b"\x89PNG") for image in images)


@pytest.mark.parametrize(
    ("answer", "status", "message"),
    [
        ({"error": "model not found"}, 404, "error 404"),
        ({"message": {"content": "{not json"}, "done_reason": "stop"}, 200, "invalid JSON"),
        ({"message": {"content": "{}"}, "done_reason": "length"}, 200, "length"),
    ],
    ids=["server-error", "not-json", "cut-off"],
)
def test_unusable_answers_are_extraction_errors(answer, status, message):
    with pytest.raises(ExtractionError, match=message):
        OllamaLlm(server(answer, status)).complete(request())


def test_unreachable_server_is_an_extraction_error():
    def refuse(incoming):
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(refuse))
    with pytest.raises(ExtractionError, match="not reachable"):
        OllamaLlm(client).complete(request())


def test_amounts_and_dates_get_the_prompts_format_as_a_pattern():
    schema = with_formats(WireInvoice.model_json_schema())
    gross = schema["properties"]["gross_total"]["anyOf"]
    price = schema["$defs"]["WireLine"]["properties"]["unit_price"]["anyOf"]
    issued = schema["properties"]["issue_date"]["anyOf"]
    assert {"type": "string", "pattern": NUMBER_PATTERN} in gross
    assert {"type": "string", "pattern": NUMBER_PATTERN} in price
    assert {"type": "string", "pattern": DATE_PATTERN} in issued
    assert {"type": "null"} in gross  # still allowed to say "not on the page"


def test_text_fields_stay_free():
    schema = with_formats(WireInvoice.model_json_schema())
    assert "pattern" not in json.dumps(schema["properties"]["number"])
    assert "pattern" not in json.dumps(schema["$defs"]["WireLine"]["properties"]["description"])


@pytest.mark.parametrize(
    ("value", "ok"),
    [("1234.56", True), ("-12.5", True), ("19", True), ("1.234,56", False), ("7 %", False)],
)
def test_number_pattern_takes_plain_decimals_only(value, ok):
    import re

    assert bool(re.fullmatch(NUMBER_PATTERN.strip("^$"), value)) is ok


def test_the_hosted_request_itself_is_unchanged():
    """The patterns are added on the way to Ollama only; the request, and
    with it the hosted model's schema and the recording key, stay the same."""
    seen = []
    plain = request()
    OllamaLlm(server(ANSWER, seen=seen)).complete(plain)
    assert plain.schema == request().schema
