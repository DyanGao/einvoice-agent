from functools import partial

from eval.generators.structured import GOLD_FILE, write_set
from eval.run import STRUCTURED, predict_structured, read_jsonl, run
from eval.score import score
from tests.conftest import StubValidator


def test_written_set_has_one_gold_record_and_file_per_document(tmp_path):
    written = write_set(tmp_path, count=2)
    records = read_jsonl(tmp_path / GOLD_FILE)
    assert (written, len(records), all((tmp_path / r["file"]).exists() for r in records)) == (
        6,
        6,
        True,
    )


def test_run_then_score_is_perfect_when_the_validator_agrees(tmp_path, accepting):
    write_set(tmp_path, count=2)  # the first two invoices carry no defect
    predictions = read_jsonl(
        run(tmp_path, STRUCTURED, partial(predict_structured, validator=accepting))
    )
    assert score(read_jsonl(tmp_path / GOLD_FILE), predictions).gate_passed


def test_unreachable_validator_fails_the_gate(tmp_path):
    write_set(tmp_path, count=1)
    predictions = read_jsonl(
        run(
            tmp_path, STRUCTURED, partial(predict_structured, validator=StubValidator(error="down"))
        )
    )
    assert not score(read_jsonl(tmp_path / GOLD_FILE), predictions).gate_passed


def test_extractor_failure_is_recorded_as_an_empty_prediction():
    from app.extract import ExtractionError
    from eval.run import predict_with_extractor

    class Failing:
        def extract(self, data):
            raise ExtractionError("model stopped with 'refusal'")

    assert predict_with_extractor(b"x", Failing()) == {
        "invoice": None,
        "error": "model stopped with 'refusal'",
    }


def test_missing_recording_stops_a_replay_run(tmp_path):
    import pytest

    from app.extract import MissingRecording
    from eval.run import build_llm_extractor, predict_with_extractor
    from tests.detect.pdf_factory import make_pdf

    extractor = build_llm_extractor("replay", tmp_path, "claude-opus-5-5", None)
    with pytest.raises(MissingRecording):
        predict_with_extractor(make_pdf(text="Rechnung Nr. 1 Gesamtbetrag 119,00 EUR"), extractor)


# --- repeated model runs (EVAL_PLAN §5: 3 runs, mean ± std) ----------------


def test_first_run_keeps_the_existing_recordings_and_predictions():
    from pathlib import Path

    from eval.run import recordings_dir, run_system

    assert recordings_dir(Path("rec"), "m", 1) == Path("rec/m")
    assert run_system("llm-rules", 1) == "llm-rules"


def test_later_runs_have_their_own_recordings_and_predictions():
    """A recording is found by the request's hash, so a second run in the same
    folder would only replay the first."""
    from pathlib import Path

    from eval.run import predictions_file, recordings_dir, run_system

    assert recordings_dir(Path("rec"), "m", 2) == Path("rec/m/run-2")
    assert predictions_file(run_system("llm", 3)) == "predictions-llm-run3.jsonl"


def test_ocr_text_systems_keep_their_recordings_apart():
    from pathlib import Path

    from eval.run import LLM_TEXT, recordings_dir

    assert recordings_dir(Path("rec"), "m", 1, LLM_TEXT) == Path("rec/m/ocr-text")


def test_a_run_can_be_limited_to_the_held_out_split(tmp_path):
    from eval.run import run

    write_set(tmp_path, 3)
    gold = read_jsonl(tmp_path / GOLD_FILE)
    for position, record in enumerate(gold):
        record["split"] = "test" if position == 0 else "dev"
    (tmp_path / GOLD_FILE).write_text("".join(__import__("json").dumps(r) + "\n" for r in gold))
    out = run(tmp_path, "x", lambda data: {"invoice": None, "error": None}, split="test")
    assert [p["id"] for p in read_jsonl(out)] == [gold[0]["id"]]
