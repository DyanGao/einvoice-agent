"""Collect the eval results into the README table and the eval report.

    python -m eval.report            # print the table, write eval/out/results.json
    python -m eval.report --write    # also rewrite the generated blocks in
                                     # README.md and docs/eval-report.md

Reads what the evals left in eval/out (run `make eval-full` first):
extraction is scored here from gold.jsonl and predictions-<system>.jsonl on
the held-out split; matching, exceptions, the decision sweep and the
injection probes are read from the reports those evals write; cost comes
from the token counts in each document's model recording.

A set or eval that has not run is shown as "not run", never left out. Given
the same files the output is the same text: no dates, no timings.
"""

import argparse
import json
import re
import statistics
from pathlib import Path
from urllib.parse import quote

from app.extract import LlmExtractor, ReplayLlm
from app.extract.confidence import Calibration
from app.extract.llm import recording_path
from app.settings import Settings
from eval.generators.structured import GOLD_FILE
from eval.run import (
    BASELINE,
    DEFAULT_RECORDINGS,
    LLM,
    LLM_RULES,
    LLM_TEXT,
    LLM_TEXT_RULES,
    LOCAL,
    LOCAL_RULES,
    STRUCTURED,
    predictions_file,
    read_jsonl,
    run_system,
)
from eval.score import Bucket, score
from eval.sweep import THRESHOLDS_FILE, documents

# USD per million tokens (input, output), Anthropic list price, November 2026.
PRICES = {"claude-opus-5-5": (4.0, 20.0)}
SETS = ("pdf", "scans")
MAX_RUNS = 3  # EVAL_PLAN §5: 3 runs on the model paths
# The last two: the same model with the document's text in the prompt (EVAL_PLAN §3
# ablation), recorded on the held-out split only.
EXTRACTORS = (BASELINE, LLM, LLM_RULES, LLM_TEXT, LLM_TEXT_RULES, LOCAL, LOCAL_RULES)
NOT_RUN = "not run"
README = Path("README.md")
EVAL_REPORT = Path("docs/eval-report.md")


# --- collecting ---------------------------------------------------------------


def extraction(set_dir: Path, system: str, split: str | None = "test") -> dict | None:
    """Field accuracy of one system on one set, all formats of the set together."""
    gold_file, predictions = set_dir / GOLD_FILE, set_dir / predictions_file(system)
    if not (gold_file.exists() and predictions.exists()):
        return None
    report = score(read_jsonl(gold_file), read_jsonl(predictions), split=split)
    total = Bucket()
    for bucket in report.by_format.values():
        for name in vars(total):
            setattr(total, name, getattr(total, name) + getattr(bucket, name))
    summary = total.summary()
    return {
        "documents": total.documents,
        "field_accuracy": summary["field_accuracy"],
        "document_exact_match": summary["document_exact_match"],
        "validation_correctness": summary["validation_correctness"],
        "wrong_by_field": dict(report.wrong_by_field.most_common()),
        "failures": [
            {"id": failure["id"], "wrong_fields": failure["wrong_fields"]}
            for failure in report.failures
        ],
    }


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    if model not in PRICES:
        return None
    per_input, per_output = PRICES[model]
    return (input_tokens * per_input + output_tokens * per_output) / 1_000_000


def usage(set_dir: Path, recordings: Path, model: str, effort: str | None) -> dict | None:
    """Tokens of the model calls for a set's documents (all splits: the cost of
    reading a document does not depend on which split it is in)."""
    gold_file = set_dir / GOLD_FILE
    if not gold_file.exists():
        return None
    directory = recordings / model
    extractor = LlmExtractor(ReplayLlm(directory), model=model, effort=effort)
    documents = recorded = input_tokens = output_tokens = 0
    for record in read_jsonl(gold_file):
        documents += 1
        path = recording_path(directory, extractor.request((set_dir / record["file"]).read_bytes()))
        if path.exists():
            answer = json.loads(path.read_text(encoding="utf-8"))
            recorded += 1
            input_tokens += answer["input_tokens"]
            output_tokens += answer["output_tokens"]
    if not recorded:
        return None
    total = cost_usd(model, input_tokens, output_tokens)
    return {
        "model": model,
        "documents": documents,
        "recorded": recorded,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "usd_per_document": None if total is None else total / recorded,
    }


def micro_f1(by_kind: dict) -> float | None:
    """Detections and false alarms pooled over all exception kinds."""
    detected = sum(row["detected"] for row in by_kind.values())
    cases = sum(row["cases"] for row in by_kind.values())
    alarms = sum(row["false_alarms"] for row in by_kind.values())
    if not detected:
        return None
    precision, recall = detected / (detected + alarms), detected / cases
    return 2 * precision * recall / (precision + recall)


def residual_errors(out: Path) -> list[dict] | None:
    """Every document the application's pipeline still misread, on both
    splits, and what kept it from being booked at the current threshold."""
    sets = [out / name for name in SETS if (out / name / predictions_file(LLM_RULES)).exists()]
    if not sets:
        return None
    threshold = json.loads(THRESHOLDS_FILE.read_text(encoding="utf-8"))["min_field_confidence"]
    return [
        {
            "set": doc.set_name,
            "split": doc.split,
            "id": doc.id,
            "wrong": sorted(doc.wrong),
            "held_back_by": doc.causes(threshold),
        }
        for doc in documents(sets, LLM_RULES, Calibration.load())
        if doc.wrong
    ]


def spread(values: list[float]) -> tuple[float, float | None]:
    """Mean and sample standard deviation; no deviation from a single run."""
    return statistics.fmean(values), statistics.stdev(values) if len(values) > 1 else None


def runs(set_dir: Path, system: str) -> list[dict]:
    """Extraction results of every recorded run, run 1 first."""
    found = []
    for run in range(1, MAX_RUNS + 1):
        result = extraction(set_dir, run_system(system, run))
        if result is None:
            break
        found.append(result)
    return found


def same_sets(decisions: list[dict]) -> list[dict]:
    """Repeated decision sweeps must cover the same sets: a PDF-only sweep next
    to PDF-and-scan ones (left by `make eval-decision-pdf` after `make
    eval-decision`) would mix numbers on different documents."""
    covered = {tuple(d["test"]["by_set"]) for d in decisions}
    if len(covered) > 1:
        raise ValueError(
            f"decision sweeps cover different sets {sorted(covered)}: run the same sweep for "
            "every run (make eval-decision and make eval-runs, or the -pdf targets)"
        )
    return decisions


def _read(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def collect(out: Path, recordings: Path, model: str, effort: str | None) -> dict:
    structured = next(
        (
            result
            for folder in ("structured", "structured-quick")
            if (result := extraction(out / folder, STRUCTURED, split=None)) is not None
        ),
        None,
    )
    exceptions = _read(out / "exceptions" / "checks-test.json")
    return {
        "model": model,
        "structured": structured,
        "extraction": {
            name: {system: extraction(out / name, system) for system in EXTRACTORS} for name in SETS
        },
        "usage": {name: usage(out / name, recordings, model, effort) for name in SETS},
        "matching": {
            system: _read(out / "world" / f"matching-{system}-test.json")
            for system in ("baseline", "matcher")
        },
        "exceptions": exceptions,
        "exceptions_f1": None if exceptions is None else micro_f1(exceptions["by_kind"]),
        "decision": {
            system: _read(out / f"decision-{system}.json") for system in (BASELINE, LLM_RULES)
        },
        "runs": {
            name: {system: runs(out / name, system) for system in (LLM, LLM_RULES)} for name in SETS
        },
        "decision_runs": same_sets(
            [
                found
                for run in range(1, MAX_RUNS + 1)
                if (found := _read(out / f"decision-{run_system(LLM_RULES, run)}.json")) is not None
            ]
        ),
        "probes": _read(out / "probes" / "report.json"),
        "residual_errors": residual_errors(out),
    }


# --- rendering ----------------------------------------------------------------


def pct(value: float | None, digits: int = 1) -> str:
    return NOT_RUN if value is None else f"{value * 100:.{digits}f} %"


def _bold(text: str) -> str:
    return text if text == NOT_RUN else f"**{text}**"


def auto_cell(point: dict | None) -> str:
    """Share booked without a person, and whether ≤ 1 % error is shown for it."""
    if point is None:
        return NOT_RUN
    rate, upper = pct(point["auto_rate"]), point["error_upper_95"]
    if upper is None:
        return f"{rate}: every invoice sent to review"
    if upper <= 0.01:
        return f"{rate} at ≤ 1 % error (95 % upper bound {pct(upper)})"
    return (
        f"{rate} auto, {point['errors']} wrong of {point['auto']}; ≤ 1 % not shown "
        f"(95 % upper bound {pct(upper)} on {point['documents']} documents)"
    )


def _accuracy(results: dict, name: str, system: str) -> str:
    repeated = results.get("runs", {}).get(name, {}).get(system, [])
    if len(repeated) > 1:
        mean, std = spread([r["field_accuracy"] for r in repeated])
        return f"{mean * 100:.2f} ± {std * 100:.2f} %"
    cell = results["extraction"][name][system]
    return NOT_RUN if cell is None else pct(cell["field_accuracy"], 2)


def _accuracy_pair(results: dict, system: str) -> str:
    cells = [_accuracy(results, name, system) for name in SETS]
    if all(cell == NOT_RUN for cell in cells):
        return NOT_RUN
    return " / ".join(cells)


def model_runs(results: dict) -> int:
    """How many model runs the extraction numbers rest on (at least one)."""
    counts = [len(found) for found in results.get("runs", {}).get("pdf", {}).values()]
    return max(counts, default=1) or 1


def auto_runs_cell(decisions: list[dict]) -> str:
    """Auto-rate over repeated runs. Errors are summed and the bound is the
    worst run's: the runs read the same documents, so they are not pooled."""
    points = [d["test"]["operating_point"] for d in decisions]
    mean, std = spread([p["auto_rate"] for p in points])
    errors = sum(p["errors"] for p in points)
    uppers = [p["error_upper_95"] for p in points if p["error_upper_95"] is not None]
    worst = max(uppers) if uppers else None
    text = f"{mean * 100:.1f} ± {std * 100:.1f} % auto over {len(points)} runs, {errors} wrong"
    if worst is None:
        return text
    if worst <= 0.01:
        return f"{text}; ≤ 1 % error (95 % upper bound {pct(worst)})"
    return (
        f"{text}; ≤ 1 % not shown (95 % upper bound {pct(worst)} on "
        f"{points[0]['documents']} documents per run)"
    )


def _matched(report: dict | None) -> str:
    return NOT_RUN if report is None else pct(report["overall"]["all invoices"]["all"])


def _test_point(decision: dict | None) -> dict | None:
    return None if decision is None else decision["test"]["operating_point"]


def cost_per_document(results: dict) -> float | None:
    found = [u for u in results["usage"].values() if u is not None]
    if not found or any(u["usd_per_document"] is None for u in found):
        return None
    recorded = sum(u["recorded"] for u in found)
    return sum(u["usd_per_document"] * u["recorded"] for u in found) / recorded


def _recall(exceptions: dict, kind: str) -> str:
    return pct(exceptions["by_kind"][kind]["recall"])


# --- README badges -------------------------------------------------------------
# The eval badge is generated with the results table, so it never shows a
# number the table does not. Shields renders static badges from the URL alone.

REPOSITORY = "DyanGao/einvoice-agent"  # change when the repository moves


def shield(label: str, message: str, color: str) -> str:
    def part(text: str) -> str:
        return quote(text.replace("-", "--").replace("_", "__"), safe="")

    return f"https://img.shields.io/badge/{part(label)}-{part(message)}-{color}"


def _accuracy_short(results: dict, name: str) -> str | None:
    repeated = results.get("runs", {}).get(name, {}).get(LLM_RULES, [])
    if repeated:
        return f"{statistics.mean(r['field_accuracy'] for r in repeated) * 100:.2f} %"
    cell = results["extraction"][name][LLM_RULES]
    return None if cell is None else f"{cell['field_accuracy'] * 100:.2f} %"


def readme_badges(results: dict) -> str:
    pdf, scans = _accuracy_short(results, "pdf"), _accuracy_short(results, "scans")
    found = [f"{label} {value}" for label, value in (("PDF", pdf), ("scans", scans)) if value]
    message, color = (" / ".join(found), "blue") if found else ("not run", "lightgrey")
    accuracy = shield("field accuracy (synthetic)", message, color)
    ci = f"https://github.com/{REPOSITORY}/actions/workflows/ci.yml"
    return " ".join(
        [
            f"[![CI]({ci}/badge.svg?branch=main)]({ci})",
            f"[![Field accuracy on synthetic held-out invoices]({accuracy})](docs/eval-report.md)",
            f"[![License: Apache 2.0]({shield('license', 'Apache-2.0', 'blue')})](LICENSE)",
            f"![EN 16931 / XRechnung]({shield('EN 16931', 'XRechnung · ZUGFeRD', 'blue')})",
        ]
    )


def readme_table(results: dict) -> str:
    structured = results["structured"]
    exceptions, f1 = results["exceptions"], results["exceptions_f1"]
    matching = results["matching"]["matcher"]
    probes = results["probes"]
    cost = cost_per_document(results)
    rows = [
        (
            "Field accuracy, e-invoices (XRechnung, ZUGFeRD)",
            "–",
            NOT_RUN
            if structured is None
            else f"**{pct(structured['field_accuracy'], 2)}** ({structured['documents']} documents,"
            f" validator verdict right {pct(structured['validation_correctness'], 2)})",
        ),
        (
            "Field accuracy, PDF / scan",
            _accuracy_pair(results, BASELINE),
            _bold(_accuracy_pair(results, LLM_RULES)),
        ),
        (
            "Order, delivery and payments all matched right",
            _matched(results["matching"]["baseline"]),
            NOT_RUN
            if matching is None
            else f"**{_matched(matching)}** ({matching['overall']['all invoices']['invoices']}"
            " invoices)",
        ),
        (
            "Exception detection F1",
            "–",
            NOT_RUN
            if f1 is None
            else f"**{f1:.2f}** (recall: changed IBAN {_recall(exceptions, 'iban')},"
            f" duplicate {_recall(exceptions, 'duplicate')})",
        ),
        (
            "Booked without a person",
            auto_cell(_test_point(results["decision"][BASELINE])),
            auto_runs_cell(results["decision_runs"])
            if len(results.get("decision_runs", [])) > 1
            else auto_cell(_test_point(results["decision"][LLM_RULES])),
        ),
        (
            "Injection probes that changed a field, route or match",
            "–",
            NOT_RUN if probes is None else f"**{probes['successes']} of {probes['probes']}**",
        ),
        (
            "Model cost per PDF or scan (list price)",
            "–",
            NOT_RUN if cost is None else f"**{cost:.3f} USD**",
        ),
    ]
    lines = ["| Metric | Rule-based baseline | **einvoice-agent** |", "|---|---|---|"]
    lines += [f"| {name} | {baseline} | {agent} |" for name, baseline, agent in rows]
    thresholds = results["decision"][LLM_RULES]
    lines += [
        "",
        "Synthetic data only, held-out split; "
        + (
            "one model run"
            if model_runs(results) == 1
            else f"{model_runs(results)} model runs, mean ± sample standard deviation"
        )
        + ", answered from recordings "
        f"(`{results['model']}`"
        + ("" if thresholds is None else f", thresholds {thresholds['version']}")
        + "). Generated by `make eval-report`; method, breakdowns and what these numbers do not "
        "show: [docs/eval-report.md](docs/eval-report.md).",
    ]
    return "\n".join(lines)


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return "\n".join(lines + ["| " + " | ".join(row) + " |" for row in rows])


def details(results: dict) -> str:
    """The breakdowns behind the README table, for docs/eval-report.md."""
    parts = ["### Reading: field accuracy on the held-out split"]
    rows = []
    for name in SETS:
        for system in EXTRACTORS:
            r = results["extraction"][name][system]
            rows.append(
                [name, system]
                + (
                    [NOT_RUN, "", ""]
                    if r is None
                    else [
                        str(r["documents"]),
                        pct(r["field_accuracy"], 2),
                        pct(r["document_exact_match"]),
                    ]
                )
            )
    parts.append(_table(["set", "system", "documents", "field accuracy", "all fields right"], rows))
    parts.append(
        "`baseline`: rules and templates over the text layer (OCR for scans). `llm`: the model "
        "alone. `llm-rules`: the model followed by the completion rules, which is what the "
        "application runs. `llm-text`, `llm-text-rules`: the same with the document's text (text "
        "layer, or OCR for scans) added to the prompt, run 1 of the model only (ablation)."
    )
    for name in SETS:
        alone = results["extraction"][name][LLM]
        if alone and alone["wrong_by_field"]:
            top = list(alone["wrong_by_field"].items())[:8]
            parts.append(f"Fields the model alone got wrong most often ({name}):")
            parts.append(_table(["field", "times wrong"], [[f"`{k}`", str(v)] for k, v in top]))

    parts.append("### Reading across repeated model runs (held-out split)")
    repeated = results.get("runs", {})
    rows = []
    for name in SETS:
        for system in (LLM, LLM_RULES):
            found = repeated.get(name, {}).get(system, [])
            if not found:
                continue
            values = [r["field_accuracy"] for r in found]
            mean, std = spread(values)
            rows.append(
                [name, system]
                + [pct(v, 2) for v in values]
                + ["–"] * (MAX_RUNS - len(values))
                + [pct(mean, 2) + ("" if std is None else f" ± {std * 100:.2f}")]
            )
    if rows:
        header = ["set", "system"] + [f"run {n}" for n in range(1, MAX_RUNS + 1)]
        parts.append(_table(header + ["mean ± std"], rows))
    else:
        parts.append(NOT_RUN)
    decisions = results.get("decision_runs", [])
    if decisions:
        points = [d["test"]["operating_point"] for d in decisions]
        parts.append(
            "Booked without a person at the operating point, per run: "
            + "; ".join(
                f"run {n} {pct(p['auto_rate'])} ({p['auto']} of {p['documents']}, "
                f"{p['errors']} wrong)"
                for n, p in enumerate(points, start=1)
            )
            + "."
        )
    parts.append(
        "Runs 2 and 3 are fresh calls to the same model with the same prompt; each run's answers "
        "are recorded, so every run replays exactly. The standard deviation is the sample one."
    )

    parts.append("### Matching: held-out invoices of the synthetic world")
    matching = results["matching"]
    if all(matching[s] is None for s in matching):
        parts.append(NOT_RUN)
    else:
        groups = [("all invoices", "overall")] + [
            (key, section)
            for section in ("by_order_reference", "by_payment")
            for key in sorted((matching["matcher"] or matching["baseline"])[section])
        ]
        rows = []
        for key, section in groups:
            cells = [section.removeprefix("by_").replace("_", " ") + ": " + key]
            for system in ("baseline", "matcher"):
                report = matching[system]
                row = None if report is None else report[section].get(key)
                cells.append(NOT_RUN if row is None else f"{pct(row['all'])} of {row['invoices']}")
            rows.append(cells)
        parts.append(_table(["invoices", "exact-key baseline", "matcher"], rows))

    parts.append("### Exceptions: one planted problem per invoice, held-out split")
    exceptions = results["exceptions"]
    if exceptions is None:
        parts.append(NOT_RUN)
    else:
        rows = [
            [
                kind,
                str(row["cases"]),
                pct(row["recall"]),
                pct(row["precision"]),
                "–" if row["f1"] is None else f"{row['f1']:.2f}",
                str(row["booked_without_review"]),
            ]
            for kind, row in exceptions["by_kind"].items()
        ]
        parts.append(
            _table(["kind", "cases", "recall", "precision", "F1", "booked without review"], rows)
        )
        clean = exceptions["clean"]
        parts.append(
            f"Clean invoices: {clean['flagged']} of {clean['cases']} flagged, "
            f"{clean['booked_without_review']} booked without review."
        )

    parts.append("### Decision: auto-rate vs. error, held-out split")
    decision = results["decision"][LLM_RULES]
    if decision is None:
        parts.append(NOT_RUN)
    else:
        section = decision["test"]
        rows = [
            [
                f"{p['threshold']}"
                + (" ← operating point" if p["threshold"] == decision["threshold"] else ""),
                f"{p['auto']} of {p['documents']}",
                pct(p["auto_rate"]),
                str(p["errors"]),
                "–" if p["error_upper_95"] is None else pct(p["error_upper_95"]),
            ]
            for p in section["curve"]
        ]
        parts.append(
            f"Sets: {', '.join(section['by_set'])}. Thresholds {decision['version']}, "
            "fitted on the dev split."
        )
        parts.append(
            _table(
                ["confidence threshold", "booked", "auto-rate", "wrong", "error, 95 % upper"], rows
            )
        )
        parts.append(
            "Held back at the operating point, by reason: "
            + ", ".join(f"{k} {v}" for k, v in section["held_back"].items())
            + "."
        )

    parts.append("### What the pipeline still misreads (both splits)")
    residual = results["residual_errors"]
    if residual is None:
        parts.append(NOT_RUN)
    elif not residual:
        parts.append("Nothing: every field of every document was read right.")
    else:
        parts.append(
            _table(
                ["set", "split", "document", "wrong", "held back by"],
                [
                    [
                        r["set"],
                        r["split"],
                        r["id"],
                        ", ".join(f"`{w}`" for w in r["wrong"]),
                        ", ".join(r["held_back_by"]) or "**nothing: booked**",
                    ]
                    for r in residual
                ],
            )
        )

    parts.append("### Injection probes")
    probes = results["probes"]
    parts.append(
        NOT_RUN
        if probes is None
        else f"{probes['successes']} of {probes['probes']} injected instructions changed a field, "
        f"a route or a match; {probes['more_scrutiny']} only sent an invoice to review that would "
        "otherwise have been booked."
    )

    parts.append("### Cost")
    rows = [
        [name]
        + (
            [NOT_RUN, "", "", ""]
            if u is None
            else [
                f"{u['recorded']} of {u['documents']}",
                f"{u['input_tokens'] / u['recorded']:.0f}",
                f"{u['output_tokens'] / u['recorded']:.0f}",
                "–" if u["usd_per_document"] is None else f"{u['usd_per_document']:.4f} USD",
            ]
        )
        for name, u in results["usage"].items()
    ]
    parts.append(
        _table(
            ["set", "recorded calls", "input tokens / doc", "output tokens / doc", "cost / doc"],
            rows,
        )
    )
    parts.append(
        f"List price of `{results['model']}` in November 2026; one call per document. "
        "E-invoices are parsed without a model and cost nothing."
    )
    return "\n\n".join(parts)


def replace_block(text: str, name: str, content: str) -> str:
    start, end = f"<!-- eval:{name}:start -->", f"<!-- eval:{name}:end -->"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(text):
        raise ValueError(f"markers {start} … {end} not found")
    return pattern.sub(lambda _: f"{start}\n{content}\n{end}", text, count=1)


def _rewrite(path: Path, name: str, content: str) -> None:
    path.write_text(
        replace_block(path.read_text(encoding="utf-8"), name, content), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("eval/out"))
    parser.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
    parser.add_argument("--write", action="store_true", help="rewrite README and eval report")
    args = parser.parse_args()
    settings = Settings.from_env()
    results = collect(
        args.out, args.recordings, settings.extractor_model, settings.extractor_effort
    )
    (args.out / "results.json").write_text(
        json.dumps(results, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    table = readme_table(results)
    print(table)
    if args.write:
        _rewrite(README, "results", table)
        _rewrite(README, "badges", readme_badges(results))
        _rewrite(EVAL_REPORT, "details", details(results))
        print(f"rewrote {README} and {EVAL_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
