"""Run the pipeline over a generated set and record what it produced.

python -m eval.run eval/out/structured
"""

import argparse
import json
import time
from pathlib import Path

import httpx

from app.pipeline import Validator, process_structured
from app.settings import Settings
from app.validate import KositValidator
from eval.generators.structured import GOLD_FILE

PREDICTIONS_FILE = "predictions.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as lines:
        return [json.loads(line) for line in lines if line.strip()]


def predict(set_dir: Path, record: dict, validator: Validator) -> dict:
    started = time.perf_counter()
    outcome = process_structured((set_dir / record["file"]).read_bytes(), validator)
    validation = outcome.validation
    return {
        "id": record["id"],
        "status": outcome.status.value,
        "valid": None if validation is None else validation.valid,
        "scenario": None if validation is None else validation.scenario,
        "error_rules": [] if validation is None else [v.rule_id for v in validation.errors],
        "error": outcome.error,
        "invoice": None if outcome.invoice is None else outcome.invoice.model_dump(mode="json"),
        "seconds": round(time.perf_counter() - started, 4),
    }


def run(set_dir: Path, validator: Validator) -> Path:
    out = set_dir / PREDICTIONS_FILE
    with out.open("w", encoding="utf-8") as predictions:
        for record in read_jsonl(set_dir / GOLD_FILE):
            prediction = predict(set_dir, record, validator)
            predictions.write(json.dumps(prediction, ensure_ascii=False, sort_keys=True) + "\n")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("set_dir", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env()
    validator = KositValidator(
        httpx.Client(base_url=settings.validator_url, timeout=settings.validator_timeout_seconds)
    )
    print(f"wrote {run(args.set_dir, validator)}")


if __name__ == "__main__":
    main()
