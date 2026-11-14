.PHONY: install lint format test check up migrate db-check test-pg test-integration eval-quick eval-full eval-pdf-baseline eval-pdf-llm eval-pdf-llm-record ocr-data eval-scans-baseline api worker ui-install ui ui-build ui-test calibrate eval-calibration seed erp eval-matching eval-exceptions decide-fit eval-decision eval-decision-pdf eval-probes eval-probes-record watch deliver

install:
	uv sync
	uv run playwright install chromium

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest

check: lint test

up:
	docker compose up -d --wait

migrate:
	uv run alembic upgrade head

# Fails if the tables in app/db differ from what the migrations create.
db-check: migrate
	uv run alembic check

# Same test suite against Postgres instead of in-memory SQLite.
test-pg:
	docker compose exec -T postgres psql -U einvoice -d einvoice -qc "DROP DATABASE IF EXISTS einvoice_test" -c "CREATE DATABASE einvoice_test"
	TEST_DATABASE_URL=postgresql+psycopg://einvoice:einvoice@127.0.0.1:5433/einvoice_test uv run pytest

# Real sample files through the running validator (make up).
test-integration:
	uv run pytest -m integration

# Structured-path eval against the running validator (make up). Fails unless
# field accuracy and validation correctness are both 100%.
eval-quick:
	uv run python -m eval.generators.structured --count 17 --out eval/out/structured-quick
	uv run python -m eval.run eval/out/structured-quick
	uv run python -m eval.score eval/out/structured-quick --gate

eval-full:
	uv run python -m eval.generators.structured --count 200 --out eval/out/structured
	uv run python -m eval.run eval/out/structured
	uv run python -m eval.score eval/out/structured --gate

# Rule-based baseline on the synthetic text PDFs (no services needed).
eval-pdf-baseline:
	uv run python -m eval.generators.pdf --count 150 --out eval/out/pdf
	uv run python -m eval.run eval/out/pdf --system baseline
	uv run python -m eval.score eval/out/pdf --system baseline --split dev
	uv run python -m eval.score eval/out/pdf --system baseline --split test

# Vision-model extractor on the same PDFs, answered from stored recordings
# (free, no credentials). "llm" is the model alone, "llm-rules" the model
# followed by the completion rules, which is what the application runs.
eval-pdf-llm:
	uv run python -m eval.generators.pdf --count 150 --out eval/out/pdf
	uv run python -m eval.run eval/out/pdf --system llm --llm-mode replay
	uv run python -m eval.run eval/out/pdf --system llm-rules --llm-mode replay
	uv run python -m eval.score eval/out/pdf --system llm --split dev
	uv run python -m eval.score eval/out/pdf --system llm --split test
	uv run python -m eval.score eval/out/pdf --system llm-rules --split dev
	uv run python -m eval.score eval/out/pdf --system llm-rules --split test

# Calls the model for every PDF without a recording. COSTS MONEY (about 0.03 USD
# per PDF in November 2026); reads ANTHROPIC_API_KEY from .env.
eval-pdf-llm-record:
	uv run python -m eval.generators.pdf --count 150 --out eval/out/pdf
	uv run --env-file .env python -m eval.run eval/out/pdf --system llm --llm-mode record

# Confidence calibration (needs the predictions of eval-pdf-llm and the scan
# set with its recordings, which only replay on the machine that made them).
# `calibrate` rewrites app/extract/calibration.json from the dev split;
# `eval-calibration` checks it on the held-out split.
calibrate:
	uv run python -m eval.calibrate fit eval/out/pdf eval/out/scans

eval-calibration:
	uv run python -m eval.calibrate report eval/out/pdf eval/out/scans

# Tesseract language models (German + English), pinned, into a local folder so
# every machine and CI recognise text with the same data.
TESSDATA_URL = https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0
ocr-data:
	mkdir -p .tessdata
	for lang in deu eng; do \
		[ -f .tessdata/$$lang.traineddata ] || curl -fsSL -o .tessdata/$$lang.traineddata $(TESSDATA_URL)/$$lang.traineddata; \
	done

# Rule-based baseline on degraded scans (needs tesseract and `make ocr-data`).
eval-scans-baseline: ocr-data
	uv run python -m eval.generators.scans --count 100 --out eval/out/scans
	uv run python -m eval.run eval/out/scans --system baseline
	uv run python -m eval.score eval/out/scans --system baseline --split dev
	uv run python -m eval.score eval/out/scans --system baseline --split test

# HTTP API on localhost only (no authentication yet).
api:
	uv run uvicorn --factory app.api.main:create_app_from_env --host 127.0.0.1 --port 8000

# Background worker; reads REDIS_URL like the API does. SimpleWorker runs jobs
# in-process (no fork), which also works on macOS.
worker:
	uv run python -m app.worker

# Asks the ERP every BANK_WATCH_SECONDS for bank transfers it has not seen and
# matches the open invoices of the suppliers they went to (needs ERP_URL).
watch:
	uv run python -m app.watch

# Books every approved or auto-decided invoice, exactly once, every
# DELIVERY_SECONDS: in the ERP (ERP_URL; the mock ERP: make erp) and in the
# DATEV-style CSV booking lists (EXPORT_DIR) and a signed webhook
# (WEBHOOK_URL + WEBHOOK_SECRET), whichever are set.
deliver:
	uv run python -m app.deliver

# Review UI (Next.js, in ui/). `make ui` expects the API from `make api`.
ui-install:
	pnpm --dir ui install --frozen-lockfile

ui:
	pnpm --dir ui dev

# Type-checks and builds the production bundle.
ui-build:
	pnpm --dir ui build

# Browser tests. They start their own in-memory API (tests/ui_server.py) and
# need a browser once: pnpm --dir ui exec playwright install chromium
ui-test:
	pnpm --dir ui test:e2e

# Mock ERP: fills its database (SQLite under data/mock_erp/) with the synthetic
# matching world and writes the ground truth for the eval to eval/out/world.
# Replaces whatever the mock ERP held before.
seed:
	uv run python -m mock_erp.seed

# Read API of the mock ERP on localhost only (no authentication).
erp:
	uv run uvicorn --factory mock_erp.main:create_app_from_env --host 127.0.0.1 --port 8100

# Matching against the synthetic world's ground truth: exact-key baseline, then
# the rule-based matcher on the held-out split. No services, no model. Fails
# if fewer than 95 % of the held-out invoices are matched fully correctly.
eval-matching:
	uv run python -m eval.match --system baseline --split test
	uv run python -m eval.match --split test --gate 0.95

# The checks on the exception set: 120 invoices with one planted problem each
# and 120 clean ones. Fails if a changed bank account or a duplicate is missed
# in more than 5 % of the held-out cases.
eval-exceptions:
	uv run python -m eval.exceptions --split dev
	uv run python -m eval.exceptions --split test --gate 0.95

# The decision engine's confidence threshold (needs the predictions of
# eval-pdf-llm and of the scan set, like calibrate). `decide-fit` rewrites
# app/decide/thresholds.json from the dev split; `eval-decision` reports the
# auto-rate vs. error curve on both splits, and the baseline extractor as a
# stress test of the gate. `eval-decision-pdf` is what CI can replay: text
# PDFs only; it warns when the auto-rate drops more than 5 points.
decide-fit:
	uv run python -m eval.sweep fit eval/out/pdf eval/out/scans

eval-decision:
	uv run python -m eval.sweep report eval/out/pdf eval/out/scans
	uv run python -m eval.sweep report eval/out/pdf eval/out/scans --system baseline

eval-decision-pdf:
	uv run python -m eval.sweep report eval/out/pdf

# Injection probes: 20 invoices with text that tries to steer the extraction
# model, each next to a control without it, through the application's own
# extraction, matching, checks and decision. Replays recorded model answers;
# fails if any injection changed a field, a route or a match.
# `eval-probes-record` calls the model for probes not recorded yet (~40 calls).
eval-probes:
	uv run python -m eval.probes --gate

eval-probes-record:
	uv run --env-file .env python -m eval.probes --llm-mode record
