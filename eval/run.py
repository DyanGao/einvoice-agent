"""Run a system over a generated set and record what it produced.

    python -m eval.run eval/out/structured
    python -m eval.run eval/out/pdf --system baseline
    python -m eval.run eval/out/pdf --system llm --llm-mode replay

Systems: "structured" is the validator + parser path, "baseline" the
rule-based extractor, "llm" the vision-model extractor.

The llm system answers from stored recordings by default (free and
deterministic). --llm-mode record calls the model for every request that has
no recording yet and stores the answer; that costs money and needs credentials.
"""

import argparse
import json
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

import anthropic
import httpx

from app.extract import (
    AnthropicLlm,
    BaselineExtractor,
    ExtractionError,
    Extractor,
    LlmExtractor,
    MissingRecording,
    RecordingLlm,
    ReplayLlm,
)
from app.pipeline import Validator, process_structured
from app.settings import Settings
from app.validate import KositValidator
from eval.generators.structured import GOLD_FILE

STRUCTURED = "structured"
BASELINE = "baseline"
LLM = "llm"
DEFAULT_RECORDINGS = Path("eval/recordings")


def predictions_file(system: str) -> str:
    return f"predictions-{system}.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as lines:
        return [json.loads(line) for line in lines if line.strip()]


def predict_structured(data: bytes, validator: Validator) -> dict:
    outcome = process_structured(data, validator)
    validation = outcome.validation
    return {
        "status": outcome.status.value,
        "valid": None if validation is None else validation.valid,
        "scenario": None if validation is None else validation.scenario,
        "error_rules": [] if validation is None else [v.rule_id for v in validation.errors],
        "error": outcome.error,
        "invoice": None if outcome.invoice is None else outcome.invoice.model_dump(mode="json"),
    }


def predict_with_extractor(data: bytes, extractor: Extractor) -> dict:
    """An extractor that gives no answer scores as an empty prediction, with
    the reason recorded; one failing document must not stop the run."""
    try:
        return {"invoice": extractor.extract(data).model_dump(mode="json"), "error": None}
    except MissingRecording:
        raise  # a replay run with gaps would report numbers for a different set
    except ExtractionError as exc:
        return {"invoice": None, "error": str(exc)}


def build_llm_extractor(mode: str, recordings: Path, model: str, effort: str | None) -> Extractor:
    directory = recordings / model
    if mode == "record":
        llm = RecordingLlm(AnthropicLlm(anthropic.Anthropic()), directory)
    else:
        llm = ReplayLlm(directory)
    return LlmExtractor(llm, model=model, effort=effort)


def run(set_dir: Path, system: str, predict: Callable[[bytes], dict]) -> Path:
    """`predict` maps a document's bytes to a prediction record."""
    out = set_dir / predictions_file(system)
    with out.open("w", encoding="utf-8") as predictions:
        for record in read_jsonl(set_dir / GOLD_FILE):
            started = time.perf_counter()
            prediction = predict((set_dir / record["file"]).read_bytes())
            prediction |= {"id": record["id"], "seconds": round(time.perf_counter() - started, 4)}
            predictions.write(json.dumps(prediction, ensure_ascii=False, sort_keys=True) + "\n")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("set_dir", type=Path)
    parser.add_argument("--system", choices=(STRUCTURED, BASELINE, LLM), default=STRUCTURED)
    parser.add_argument("--llm-mode", choices=("replay", "record"), default="replay")
    parser.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
    args = parser.parse_args()
    if args.system == BASELINE:
        predict = partial(predict_with_extractor, extractor=BaselineExtractor())
    elif args.system == LLM:
        settings = Settings.from_env()
        extractor = build_llm_extractor(
            args.llm_mode, args.recordings, settings.extractor_model, settings.extractor_effort
        )
        predict = partial(predict_with_extractor, extractor=extractor)
    else:
        settings = Settings.from_env()
        client = httpx.Client(
            base_url=settings.validator_url, timeout=settings.validator_timeout_seconds
        )
        predict = partial(predict_structured, validator=KositValidator(client))
    print(f"wrote {run(args.set_dir, args.system, predict)}")


if __name__ == "__main__":
    main()
