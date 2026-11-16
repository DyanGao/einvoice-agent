"""The eval report: every number in the README comes from here, so the
arithmetic is checked against values computed by hand."""

import copy
import json
from pathlib import Path

import pytest

from app.extract import LlmExtractor, ReplayLlm
from app.extract.llm import recording_path
from eval.generators.structured import generate
from eval.report import (
    auto_cell,
    collect,
    cost_usd,
    details,
    extraction,
    micro_f1,
    readme_table,
    replace_block,
    usage,
)
from eval.score import flatten

DOCUMENT = next(generate(1))
FIELDS = len(flatten(DOCUMENT.invoice))
MODEL = "claude-opus-5-5"


def gold(doc_id, split, fmt="pdf_text", file=None):
    record = copy.deepcopy(DOCUMENT.gold_record())
    return record | {
        "id": doc_id,
        "split": split,
        "format": fmt,
        "file": file or f"{doc_id}.pdf",
        "expected_valid": None,  # PDFs and scans have no validator verdict
        "expected_rule": None,
    }


def write_jsonl(path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def predicted(record, **changes):
    invoice = copy.deepcopy(record["invoice"]) | changes
    return {"id": record["id"], "invoice": invoice, "error": None, "seconds": 0.1}


@pytest.fixture
def pdf_set(tmp_path):
    folder = tmp_path / "pdf"
    folder.mkdir()
    records = [gold("a", "test"), gold("b", "test"), gold("c", "dev")]
    for record in records:
        (folder / record["file"]).write_bytes(b"%PDF-1.4\n%" + record["id"].encode() + b"\n")
    write_jsonl(folder / "gold.jsonl", records)
    write_jsonl(
        folder / "predictions-llm-rules.jsonl",
        [predicted(records[0]), predicted(records[1], currency="USD"), predicted(records[2])],
    )
    return folder


def test_field_accuracy_counts_every_field_of_the_held_out_documents_only(pdf_set):
    result = extraction(pdf_set, "llm-rules")
    assert result["documents"] == 2
    assert result["field_accuracy"] == pytest.approx((2 * FIELDS - 1) / (2 * FIELDS))
    assert result["document_exact_match"] == 0.5
    assert result["wrong_by_field"] == {"currency": 1}


def test_set_without_predictions_for_that_system_is_not_run(pdf_set):
    assert extraction(pdf_set, "baseline") is None


def test_cost_is_tokens_times_list_price():
    # 1M input at 4 USD, 0.5M output at 20 USD per million tokens.
    assert cost_usd(MODEL, 1_000_000, 500_000) == pytest.approx(14.0)


def test_cost_of_a_model_without_a_known_price_is_unknown():
    assert cost_usd("some-other-model", 10, 10) is None


def test_usage_reads_the_tokens_of_each_documents_recording(tmp_path):
    folder, recordings = tmp_path / "set", tmp_path / "recordings"
    folder.mkdir()
    pdf = b"%PDF-1.4\n%synthetic\n"
    (folder / "a.pdf").write_bytes(pdf)
    (folder / "b.pdf").write_bytes(pdf + b"%other\n")
    write_jsonl(folder / "gold.jsonl", [gold("a", "test"), gold("b", "test")])
    request = LlmExtractor(ReplayLlm(recordings / MODEL), model=MODEL).request(pdf)
    path = recording_path(recordings / MODEL, request)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"input_tokens": 6000, "output_tokens": 700}))

    result = usage(folder, recordings, MODEL, None)

    assert (result["documents"], result["recorded"]) == (2, 1)
    assert (result["input_tokens"], result["output_tokens"]) == (6000, 700)
    assert result["usd_per_document"] == pytest.approx((6000 * 4 + 700 * 20) / 1e6)


def test_micro_f1_pools_detections_and_false_alarms_over_all_kinds():
    by_kind = {
        "iban": {"cases": 10, "detected": 9, "false_alarms": 1},
        "duplicate": {"cases": 10, "detected": 10, "false_alarms": 0},
    }
    # precision 19/20, recall 19/20
    assert micro_f1(by_kind) == pytest.approx(0.95)


def point(auto, documents, errors, upper):
    return {
        "auto": auto,
        "documents": documents,
        "auto_rate": auto / documents,
        "errors": errors,
        "error_upper_95": upper,
    }


def test_auto_rate_says_when_one_percent_error_is_not_demonstrated():
    cell = auto_cell(point(45, 60, 0, 0.064))
    assert "75.0 %" in cell
    assert "not shown" in cell
    assert "6.4 %" in cell


def test_auto_rate_is_claimed_at_one_percent_only_when_the_bound_is_below_it():
    cell = auto_cell(point(900, 1000, 0, 0.0033))
    assert "not shown" not in cell
    assert "90.0 %" in cell


def test_report_on_an_empty_folder_marks_everything_not_run(tmp_path):
    table = readme_table(collect(tmp_path, tmp_path / "recordings", MODEL, None))
    rows = [line for line in table.splitlines() if line.startswith("| ") and "Metric" not in line]
    assert rows
    assert all("not run" in row for row in rows)


def test_report_is_the_same_text_for_the_same_predictions(pdf_set):
    out = pdf_set.parent
    first = readme_table(collect(out, out / "recordings", MODEL, None))
    second = readme_table(collect(out, out / "recordings", MODEL, None))
    assert first == second


def test_generated_block_is_replaced_and_the_rest_kept():
    text = "intro\n<!-- eval:table:start -->\nold\n<!-- eval:table:end -->\noutro\n"
    once = replace_block(text, "table", "new")
    assert once == "intro\n<!-- eval:table:start -->\nnew\n<!-- eval:table:end -->\noutro\n"
    assert replace_block(once, "table", "new") == once


def test_file_without_the_markers_is_an_error():
    with pytest.raises(ValueError, match="eval:table:start"):
        replace_block("no markers here", "table", "new")


def test_threshold_that_books_nothing_has_no_error_bound_and_is_not_called_not_run():
    curve = [{"threshold": 0.99, **point(0, 60, 0, None)}]
    decision = {
        "threshold": 0.99,
        "version": "v",
        "test": {
            "curve": curve,
            "by_set": {"pdf": {}},
            "held_back": {},
            "operating_point": curve[0],
        },
    }
    results = collect(Path("/nonexistent"), Path("/nonexistent"), MODEL, None)
    results["decision"]["llm-rules"] = decision
    row = next(line for line in details(results).splitlines() if line.startswith("| 0.99"))
    assert row.endswith("| – |")


def test_misread_document_is_listed_with_what_held_it_back(pdf_set):
    (row,) = collect(pdf_set.parent, pdf_set.parent / "r", MODEL, None)["residual_errors"]
    assert (row["id"], row["split"], row["wrong"]) == ("b", "test", ["currency"])
    assert isinstance(row["held_back_by"], list)


def test_misread_document_that_nothing_held_back_is_called_booked(pdf_set):
    results = collect(pdf_set.parent, pdf_set.parent / "r", MODEL, None)
    results["residual_errors"][0]["held_back_by"] = []
    assert "**nothing: booked**" in details(results)


# --- repeated model runs: mean ± sample standard deviation ------------------


def test_spread_of_three_runs_is_mean_and_sample_standard_deviation():
    from eval.report import spread

    # mean 0.98; deviations -0.02, 0, 0.02 → variance 0.0008 / 2 → std 0.02
    mean, std = spread([0.96, 0.98, 1.00])
    assert (mean, std) == (pytest.approx(0.98), pytest.approx(0.02))


def test_one_run_has_no_spread():
    from eval.report import spread

    assert spread([0.97]) == (0.97, None)


def write_run(folder, system, run, records, changes):
    from eval.run import predictions_file, run_system

    write_jsonl(
        folder / predictions_file(run_system(system, run)),
        [predicted(r, **changes) for r in records],
    )


def test_readme_shows_mean_and_spread_when_there_are_several_runs(tmp_path):
    folder = tmp_path / "pdf"
    folder.mkdir()
    records = [gold("a", "test"), gold("b", "test")]
    write_jsonl(folder / "gold.jsonl", records)
    for record in records:
        (folder / record["file"]).write_bytes(b"%PDF-1.4\n%" + record["id"].encode() + b"\n")
    write_run(folder, "llm-rules", 1, records, {})
    write_run(folder, "llm-rules", 2, records, {})
    write_run(folder, "llm-rules", 3, records, {"currency": "USD"})
    results = collect(tmp_path, tmp_path / "recordings", MODEL, None)
    per_run = [r["field_accuracy"] for r in results["runs"]["pdf"]["llm-rules"]]
    assert per_run == [1.0, 1.0, pytest.approx(1 - 1 / FIELDS)]
    table = readme_table(results)
    assert "±" in table
    assert "3 model runs" in table


def test_readme_says_one_run_when_there_is_only_one(pdf_set):
    table = readme_table(collect(pdf_set.parent, pdf_set.parent / "r", MODEL, None))
    assert "one model run" in table
    assert "±" not in table


def test_decision_sweeps_over_different_sets_are_refused():
    from eval.report import same_sets

    pdf_only = {"test": {"by_set": {"pdf": {}}}}
    both = {"test": {"by_set": {"pdf": {}, "scans": {}}}}
    assert same_sets([both, both]) == [both, both]
    with pytest.raises(ValueError, match="different sets"):
        same_sets([pdf_only, both])
