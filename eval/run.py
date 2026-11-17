"""Run a system over a generated set and record what it produced.

    python -m eval.run eval/out/structured
    python -m eval.run eval/out/pdf --system baseline
    python -m eval.run eval/out/pdf --system llm --llm-mode replay

Systems: "structured" is the validator + parser path, "baseline" the
rule-based extractor, "llm" the vision-model extractor on its own, and
"llm-rules" the same model answers followed by the completion rules
(app/extract/complete.py) — what the application actually runs.

The llm system answers from stored recordings by default (free and
deterministic). --llm-mode record calls the model for every request that has
no recording yet and stores the answer; that costs money and needs credentials.

--run N (EVAL_PLAN: 3 runs, mean ± std) keeps run N's recordings in
recordings/<model>/run-N and writes predictions-<system>-runN.jsonl. Run 1 is
the plain folder and file, as before.
"""

import argparse
import json
import os
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

import anthropic
import httpx

from app.extract import (
    AnthropicLlm,
    BaselineExtractor,
    CompletingExtractor,
    ExtractionError,
    Extractor,
    LlmExtractor,
    MissingRecording,
    RecordingLlm,
    ReplayLlm,
)
from app.extract.confidence import document_text
from app.extract.ollama import OllamaLlm, is_local
from app.pipeline import Validator, process_structured
from app.settings import Settings
from app.validate import KositValidator
from eval.generators.structured import GOLD_FILE

STRUCTURED = "structured"
BASELINE = "baseline"
LLM = "llm"
LLM_RULES = "llm-rules"
# EVAL_PLAN §3 ablation: the same model with the document's text in the prompt.
LLM_TEXT = "llm-text"
LLM_TEXT_RULES = "llm-text-rules"
TEXT_SYSTEMS = (LLM_TEXT, LLM_TEXT_RULES)
# EVAL_PLAN §3 ablation: a local vision model (Ollama) in place of the hosted one.
LOCAL = "local"
LOCAL_RULES = "local-rules"
LOCAL_SYSTEMS = (LOCAL, LOCAL_RULES)
DEFAULT_LOCAL_MODEL = "ollama/qwen2.5vl:7b"
DEFAULT_RECORDINGS = Path("eval/recordings")


def predictions_file(system: str) -> str:
    return f"predictions-{system}.jsonl"


def run_system(system: str, run: int) -> str:
    """The name a repeated run's predictions are stored and scored under."""
    return system if run == 1 else f"{system}-run{run}"


def recordings_dir(recordings: Path, model: str, run: int, system: str = LLM) -> Path:
    """Recordings are found by the request's hash: each run needs its own folder,
    or a second run would only replay the first. The text ablation keeps its
    own folder too, so it can be told apart from the main runs."""
    base = recordings / model / "ocr-text" if system in TEXT_SYSTEMS else recordings / model
    return base if run == 1 else base / f"run-{run}"


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


def build_llm_extractor(
    mode: str,
    recordings: Path,
    model: str,
    effort: str | None,
    run: int = 1,
    system: str = LLM,
) -> Extractor:
    directory = recordings_dir(recordings, model, run, system)
    if mode == "record":
        client = OllamaLlm() if is_local(model) else AnthropicLlm(anthropic.Anthropic())
        llm = RecordingLlm(client, directory)
    else:
        llm = ReplayLlm(directory)
    text_of = document_text if system in TEXT_SYSTEMS else None
    return LlmExtractor(llm, model=model, effort=effort, text_of=text_of)


def run(
    set_dir: Path, system: str, predict: Callable[[bytes], dict], split: str | None = None
) -> Path:
    """`predict` maps a document's bytes to a prediction record; `split` limits
    the run to one split (the text ablation reads only the held-out documents)."""
    out = set_dir / predictions_file(system)
    with out.open("w", encoding="utf-8") as predictions:
        for record in read_jsonl(set_dir / GOLD_FILE):
            if split is not None and record.get("split") != split:
                continue
            started = time.perf_counter()
            prediction = predict((set_dir / record["file"]).read_bytes())
            prediction |= {"id": record["id"], "seconds": round(time.perf_counter() - started, 4)}
            predictions.write(json.dumps(prediction, ensure_ascii=False, sort_keys=True) + "\n")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("set_dir", type=Path)
    parser.add_argument(
        "--system",
        choices=(STRUCTURED, BASELINE, LLM, LLM_RULES, *TEXT_SYSTEMS, *LOCAL_SYSTEMS),
        default=STRUCTURED,
    )
    parser.add_argument("--llm-mode", choices=("replay", "record"), default="replay")
    parser.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
    parser.add_argument("--run", type=int, default=1, help="which repeated model run (1, 2, 3)")
    parser.add_argument("--split", choices=("dev", "test"), help="run only this split")
    args = parser.parse_args()
    if args.run != 1 and args.system not in (LLM, LLM_RULES, *TEXT_SYSTEMS, *LOCAL_SYSTEMS):
        parser.error("--run applies to the model systems only")
    if args.system == BASELINE:
        predict = partial(predict_with_extractor, extractor=BaselineExtractor())
    elif args.system in (LLM, LLM_RULES, *TEXT_SYSTEMS, *LOCAL_SYSTEMS):
        settings = Settings.from_env()
        local = args.system in LOCAL_SYSTEMS
        extractor = build_llm_extractor(
            args.llm_mode,
            args.recordings,
            os.environ.get("LOCAL_MODEL", DEFAULT_LOCAL_MODEL)
            if local
            else settings.extractor_model,
            None if local else settings.extractor_effort,
            args.run,
            args.system,
        )
        if args.system in (LLM_RULES, LLM_TEXT_RULES, LOCAL_RULES):
            # As in the application: the rules see the document's own text.
            extractor = CompletingExtractor(extractor, text_of=document_text)
        predict = partial(predict_with_extractor, extractor=extractor)
    else:
        settings = Settings.from_env()
        client = httpx.Client(
            base_url=settings.validator_url, timeout=settings.validator_timeout_seconds
        )
        predict = partial(predict_structured, validator=KositValidator(client))
    out = run(args.set_dir, run_system(args.system, args.run), predict, split=args.split)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
