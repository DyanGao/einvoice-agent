"""A local vision model through Ollama, as the alternative to the hosted one.

Same request, same schema, same prompt as `AnthropicLlm`: only the transport
differs, so the two are compared on equal terms (EVAL_PLAN §3, hosted vs.
local). Ollama takes images, not PDFs, so each PDF page is rendered to a PNG
first. The JSON schema goes to Ollama's structured output (`format`), and the
temperature is 0 so a recording can be replayed and run again alike.

One addition: the formats the prompt asks for (amounts as "1234.56", dates as
"2026-01-25") are written into that schema as patterns. Claude follows the
prompt; a small local model copies the printed "1.234,56" instead. Ollama
enforces the schema while the model writes, so the pattern is how it is told.

Nothing leaves the machine: the model runs in the local Ollama server.

Selected with a model name starting "ollama/", e.g. EXTRACTOR_MODEL=ollama/qwen2.5vl:7b.
"""

import base64
import io
import json
from typing import Any

import httpx
import pypdfium2 as pdfium

from app.extract.llm import ExtractionError, LlmRequest, LlmResponse

PREFIX = "ollama/"
DEFAULT_URL = "http://127.0.0.1:11434"
# Pages rendered for the model: enough for the invoices this reads (H4 sends
# longer documents to a person before any model call).
MAX_PAGES = 4
RENDER_DPI = 144
# Room for the instructions, the schema and the page images.
CONTEXT_TOKENS = 16384
TIMEOUT_SECONDS = 600


# Fields the prompt asks to write as plain decimals or ISO dates.
NUMBER_FIELDS = frozenset(
    {
        "quantity",
        "unit_price",
        "net",
        "vat_rate",
        "rate",
        "taxable_amount",
        "tax_amount",
        "line_net_total",
        "net_total",
        "vat_total",
        "gross_total",
        "amount_due",
    }
)
DATE_FIELDS = frozenset({"issue_date", "due_date"})
NUMBER_PATTERN = r"^-?[0-9]+(\.[0-9]+)?$"
DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"


def with_formats(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema with the prompt's number and date formats as patterns."""

    def walk(node: Any, name: str | None = None) -> Any:
        if isinstance(node, list):
            return [walk(item, name) for item in node]
        if not isinstance(node, dict):
            return node
        if node.get("type") == "string" and name in NUMBER_FIELDS | DATE_FIELDS:
            pattern = NUMBER_PATTERN if name in NUMBER_FIELDS else DATE_PATTERN
            return {**node, "pattern": pattern}
        out = {}
        for key, value in node.items():
            if key == "properties":
                out[key] = {field: walk(sub, field) for field, sub in value.items()}
            elif key in ("anyOf", "oneOf", "items"):
                out[key] = walk(value, name)
            elif key == "$defs":
                out[key] = {defname: walk(sub) for defname, sub in value.items()}
            else:
                out[key] = value
        return out

    return walk(schema)


def is_local(model: str) -> bool:
    return model.startswith(PREFIX)


def page_images(document: bytes, media_type: str) -> list[bytes]:
    """The document as images: a PDF page by page, an image as it is."""
    if media_type != "application/pdf":
        return [document]
    try:
        pdf = pdfium.PdfDocument(document)
    except pdfium.PdfiumError as exc:
        raise ExtractionError(f"cannot render the PDF for the local model: {exc}") from exc
    images = []
    for index in range(min(len(pdf), MAX_PAGES)):
        picture = pdf[index].render(scale=RENDER_DPI / 72).to_pil()
        buffer = io.BytesIO()
        picture.save(buffer, format="PNG")
        images.append(buffer.getvalue())
    return images


class OllamaLlm:
    """A vision model served by Ollama, with a JSON-schema-constrained answer."""

    def __init__(self, client: httpx.Client | None = None, base_url: str = DEFAULT_URL) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=TIMEOUT_SECONDS)

    def complete(self, request: LlmRequest) -> LlmResponse:
        model = request.model.removeprefix(PREFIX)
        images = [
            base64.standard_b64encode(image).decode("ascii")
            for image in page_images(request.document, request.media_type)
        ]
        body: dict[str, Any] = {
            "model": model,
            "stream": False,
            "format": with_formats(request.schema),
            "options": {"temperature": 0, "num_ctx": CONTEXT_TOKENS},
            "messages": [
                {"role": "system", "content": request.system},
                # The document only ever in the user turn, as with the hosted model.
                {"role": "user", "content": request.prompt, "images": images},
            ],
        }
        try:
            response = self._client.post("/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise ExtractionError(f"local model not reachable: {exc}") from exc
        if response.status_code != 200:
            raise ExtractionError(f"local model error {response.status_code}: {response.text}")
        answer = response.json()
        if answer.get("done_reason") not in (None, "stop"):
            raise ExtractionError(f"local model stopped with {answer.get('done_reason')!r}")
        text = (answer.get("message") or {}).get("content") or ""
        try:
            output = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"local model returned invalid JSON: {exc}") from exc
        return LlmResponse(
            output=output,
            model=request.model,
            input_tokens=answer.get("prompt_eval_count", 0),
            output_tokens=answer.get("eval_count", 0),
        )
