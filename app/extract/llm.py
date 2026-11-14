"""The one place this project talks to a language model.

Everything above this module sees an LlmClient: a function from a request to
the model's JSON. Tests and the quick eval use ReplayLlm with stored
responses, so they are deterministic and free; RecordingLlm wraps the real
client to create those recordings.
"""

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import anthropic


class ExtractionError(RuntimeError):
    """The model gave no usable answer (API failure, refusal, cut-off output)."""


class MissingRecording(ExtractionError):
    """Replay was asked for a request that has never been recorded."""


@dataclass(frozen=True)
class LlmRequest:
    model: str
    system: str
    prompt: str
    prompt_version: str
    document: bytes
    media_type: str  # application/pdf, image/png, image/jpeg
    schema: dict[str, Any]
    effort: str | None = None

    def key(self) -> str:
        """Identifies the request for record/replay: any change to model,
        instructions, schema or document produces a different key."""
        identity = {
            "model": self.model,
            "system": self.system,
            "prompt": self.prompt,
            "prompt_version": self.prompt_version,
            "media_type": self.media_type,
            "document_sha256": hashlib.sha256(self.document).hexdigest(),
            "schema": self.schema,
            "effort": self.effort,
        }
        canonical = json.dumps(identity, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LlmResponse:
    output: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int


class LlmClient(Protocol):
    def complete(self, request: LlmRequest) -> LlmResponse: ...


def _document_block(request: LlmRequest) -> dict[str, Any]:
    data = base64.standard_b64encode(request.document).decode("ascii")
    kind = "document" if request.media_type == "application/pdf" else "image"
    return {
        "type": kind,
        "source": {"type": "base64", "media_type": request.media_type, "data": data},
    }


class AnthropicLlm:
    """Claude via the Messages API with a JSON-schema-constrained response."""

    MAX_TOKENS = 16000

    def __init__(self, client: anthropic.Anthropic) -> None:
        self._client = client

    def complete(self, request: LlmRequest) -> LlmResponse:
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": request.schema}
        }
        if request.effort is not None:
            output_config["effort"] = request.effort
        try:
            message = self._client.messages.create(
                model=request.model,
                max_tokens=self.MAX_TOKENS,
                system=request.system,
                messages=[
                    {
                        "role": "user",
                        # The document goes before the instruction, and only ever in
                        # the user turn: it must never reach the system prompt.
                        "content": [
                            _document_block(request),
                            {"type": "text", "text": request.prompt},
                        ],
                    }
                ],
                output_config=output_config,
            )
        except anthropic.APIConnectionError as exc:
            raise ExtractionError(f"model not reachable: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise ExtractionError(f"model API error {exc.status_code}: {exc.message}") from exc

        if message.stop_reason != "end_turn":
            # refusal: no content to trust; max_tokens: JSON cut off mid-way.
            raise ExtractionError(f"model stopped with {message.stop_reason!r}")
        text = next((block.text for block in message.content if block.type == "text"), None)
        if text is None:
            raise ExtractionError("model returned no text block")
        try:
            output = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"model returned invalid JSON: {exc}") from exc
        return LlmResponse(
            output=output,
            model=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )


def recording_path(directory: Path, request: LlmRequest) -> Path:
    return directory / f"{request.key()}.json"


class ReplayLlm:
    """Answers from stored recordings only; never touches the network."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def complete(self, request: LlmRequest) -> LlmResponse:
        path = recording_path(self._directory, request)
        if not path.exists():
            raise MissingRecording(
                f"no recording for this request in {self._directory} (key {request.key()[:12]}…); "
                "the document, prompt, schema or model changed since recording — record again"
            )
        return LlmResponse(**json.loads(path.read_text(encoding="utf-8")))


class RecordingLlm:
    """Calls the real client and stores each answer for later replay."""

    def __init__(self, inner: LlmClient, directory: Path) -> None:
        self._inner = inner
        self._directory = directory

    def complete(self, request: LlmRequest) -> LlmResponse:
        path = recording_path(self._directory, request)
        if path.exists():  # already paid for; do not call again
            return LlmResponse(**json.loads(path.read_text(encoding="utf-8")))
        response = self._inner.complete(request)
        self._directory.mkdir(parents=True, exist_ok=True)
        record = {
            "output": response.output,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
        )
        return response
