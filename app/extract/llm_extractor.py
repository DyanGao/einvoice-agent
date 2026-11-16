import logging
from collections.abc import Callable

from pydantic import ValidationError

from app.detect import detect
from app.extract.draft import InvoiceDraft
from app.extract.llm import ExtractionError, LlmClient, LlmRequest
from app.extract.prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    TEXT_PROMPT_VERSION,
    USER_PROMPT,
    user_prompt_with_text,
)
from app.extract.wire import WireInvoice, to_draft
from app.models import DocumentFormat
from app.settings import DEFAULT_EXTRACTOR_MODEL

logger = logging.getLogger(__name__)

DEFAULT_MODEL = DEFAULT_EXTRACTOR_MODEL
_IMAGE_MEDIA_TYPES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))


def _media_type(data: bytes, fmt: DocumentFormat) -> str:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    for signature, media_type in _IMAGE_MEDIA_TYPES:
        if data.startswith(signature):
            return media_type
    raise ExtractionError(f"cannot send this document to the model (detected: {fmt.value})")


class LlmExtractor:
    """Reads a PDF or image with a vision model into an InvoiceDraft.

    Raises ExtractionError when the model gives no usable answer; it never
    returns a guess. Structured e-invoices are refused: they have a parser and
    must not be read by a model.
    """

    def __init__(
        self,
        llm: LlmClient,
        model: str = DEFAULT_MODEL,
        effort: str | None = None,
        text_of: Callable[[bytes], str | None] | None = None,
    ) -> None:
        """`text_of` (the eval's ablation only) adds the document's text to the
        prompt; without it, or without text, the request is the plain one."""
        self._llm = llm
        self._model = model
        self._effort = effort
        self._text_of = text_of

    def request(self, data: bytes) -> LlmRequest:
        """What is sent to the model for this document. Also used by the eval
        report to find a document's recording, and with it its token counts."""
        fmt = detect(data)
        if fmt.is_structured:
            raise ExtractionError(
                f"{fmt.value} is a structured e-invoice; it is parsed, not extracted"
            )
        text = self._text_of(data) if self._text_of is not None else None
        return LlmRequest(
            model=self._model,
            system=SYSTEM_PROMPT,
            prompt=USER_PROMPT if not text else user_prompt_with_text(text),
            prompt_version=PROMPT_VERSION if not text else TEXT_PROMPT_VERSION,
            document=data,
            media_type=_media_type(data, fmt),
            schema=WireInvoice.model_json_schema(),
            effort=self._effort,
        )

    def extract(self, data: bytes) -> InvoiceDraft:
        fmt = detect(data)
        response = self._llm.complete(self.request(data))
        try:
            wire = WireInvoice.model_validate(response.output)
        except ValidationError as exc:
            raise ExtractionError(f"model output does not match the schema: {exc}") from exc
        logger.info(
            "extracted with %s: %d input / %d output tokens",
            response.model,
            response.input_tokens,
            response.output_tokens,
        )
        return to_draft(wire, fmt)
