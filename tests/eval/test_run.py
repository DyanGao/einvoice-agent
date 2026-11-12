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
