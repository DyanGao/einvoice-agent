.PHONY: install lint format test check up migrate db-check test-pg test-integration eval-quick eval-structured eval-full eval-report eval-scans-llm eval-runs eval-runs-pdf eval-text eval-pdf-baseline eval-pdf-llm eval-pdf-llm-record ocr-data eval-scans-baseline api worker ui-install ui ui-build ui-test calibrate eval-calibration seed erp eval-matching eval-exceptions eval-credits decide-fit eval-decision eval-decision-pdf eval-probes eval-probes-record watch deliver demo stack demo-local master-key stack-check demo-gif eval-belege eval-local keycloak keycloak-down mailbox test-mailbox eval-mailbox german-texts german-texts-check

# The README demo in Docker: sample_invoices/ through processing, checks,
# decision and delivery into a fresh mock ERP, in a database of its own. Needs
# only Docker; no API key. Exits non-zero if the story does not happen as told.
demo:
	docker compose run --rm --build demo

# The same with uv instead of the app image (needs `make up`).
demo-local:
	uv run python -m eval.demo

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

# Postgres, Redis and the validator, for running the application with uv.
up:
	docker compose up -d --wait postgres redis validator

# The whole application in Docker: http://localhost:3000 (API :8000, mock ERP :8100).
stack:
	docker compose up -d --build --wait

# The README's demo GIF (docs/demo.gif), recorded from the running application
# with a browser. Needs `make stack` on an empty database: it refuses to add
# invoices to one in use. For a throwaway stack:
#   docker compose -p einvoice-gif up -d --build --wait && make demo-gif
#   docker compose -p einvoice-gif down -v
demo-gif:
	uv run python deploy/demo_gif.py

# Smoke test of `make stack`: one sample uploaded, processed, booked; UI up.
stack-check:
	uv run python deploy/stack_check.py

migrate:
	uv run alembic upgrade head

# Fails if the tables in app/db differ from what the migrations create.
db-check: migrate
	uv run alembic check

# Same test suite against Postgres instead of in-memory SQLite.
test-pg:
	docker compose exec -T postgres psql -U einvoice -d einvoice -qc "DROP DATABASE IF EXISTS einvoice_test" -c "CREATE DATABASE einvoice_test"
	TEST_DATABASE_URL=postgresql+psycopg://einvoice:einvoice@127.0.0.1:5433/einvoice_test \
		TEST_REDIS_URL=redis://127.0.0.1:6380/15 uv run pytest

# Real sample files through the running validator (make up).
test-integration:
	uv run pytest -m integration

# Structured-path eval against the running validator (make up). Fails unless
# field accuracy and validation correctness are both 100%.
eval-quick:
	uv run python -m eval.generators.structured --count 17 --out eval/out/structured-quick
	uv run python -m eval.run eval/out/structured-quick
	uv run python -m eval.score eval/out/structured-quick --gate

eval-structured:
	uv run python -m eval.generators.structured --count 200 --out eval/out/structured
	uv run python -m eval.run eval/out/structured
	uv run python -m eval.score eval/out/structured --gate

# Model runs 2 and 3 (EVAL_PLAN: 3 runs, mean ± std), replayed from their own
# recordings, with the decision sweep per run. Recording them is paid:
#   uv run --env-file .env python -m eval.run eval/out/<set> --system llm --llm-mode record --run N
eval-runs:
	for run in 2 3; do \
		for set in pdf scans; do \
			uv run python -m eval.run eval/out/$$set --system llm --llm-mode replay --run $$run || exit 1; \
			uv run python -m eval.run eval/out/$$set --system llm-rules --llm-mode replay --run $$run || exit 1; \
		done; \
		uv run python -m eval.sweep report eval/out/pdf eval/out/scans --system llm-rules-run$$run --gate || exit 1; \
	done

# The same for the text PDFs only: what CI can replay (scan recordings replay
# only on the machine that made them).
eval-runs-pdf:
	for run in 2 3; do \
		uv run python -m eval.run eval/out/pdf --system llm --llm-mode replay --run $$run || exit 1; \
		uv run python -m eval.run eval/out/pdf --system llm-rules --llm-mode replay --run $$run || exit 1; \
		uv run python -m eval.sweep report eval/out/pdf --system llm-rules-run$$run --gate || exit 1; \
	done

# Ablation (EVAL_PLAN §3): the model with the document's text in its prompt, on
# the held-out split, from its recordings (eval/recordings/<model>/ocr-text).
# Recording it is paid: ... --system llm-text --llm-mode record --split test
eval-text:
	for set in pdf scans; do \
		uv run python -m eval.run eval/out/$$set --system llm-text --llm-mode replay --split test || exit 1; \
		uv run python -m eval.run eval/out/$$set --system llm-text-rules --llm-mode replay --split test || exit 1; \
	done

# Every eval, then the report: README results table and docs/eval-report.md.
# Needs the validator (make up), tesseract with `make ocr-data`, and the scan
# recordings, which replay only on the machine that made them (see
# eval/generators/scans.py). Replays recorded model answers: no API key, no cost.
eval-full: eval-structured eval-pdf-baseline eval-pdf-llm eval-scans-baseline eval-scans-llm \
		eval-matching eval-exceptions eval-credits eval-decision eval-runs eval-text eval-probes eval-report

# Collects what the evals left in eval/out into results.json and rewrites the
# generated blocks of README.md and docs/eval-report.md.
eval-report:
	uv run python -m eval.report --write

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

# Vision-model extractor on the scans, from recordings (see eval-full for why
# these replay only where they were recorded).
eval-scans-llm:
	uv run python -m eval.generators.scans --count 100 --out eval/out/scans
	uv run python -m eval.run eval/out/scans --system llm --llm-mode replay
	uv run python -m eval.run eval/out/scans --system llm-rules --llm-mode replay
	uv run python -m eval.score eval/out/scans --system llm-rules --split test

# Rule-based baseline on degraded scans (needs tesseract and `make ocr-data`).
eval-scans-baseline: ocr-data
	uv run python -m eval.generators.scans --count 100 --out eval/out/scans
	uv run python -m eval.run eval/out/scans --system baseline
	uv run python -m eval.score eval/out/scans --system baseline --split dev
	uv run python -m eval.score eval/out/scans --system baseline --split test

# The services read .env when there is one (MASTER_KEY, ANTHROPIC_API_KEY, ...).
ENV_FILE = $(if $(wildcard .env),--env-file .env,)

# The master key stored invoices are encrypted with (app/crypto.py), written
# to .env once. Keep a copy outside this machine and its backups: without it
# the stored invoices cannot be read, and with it anyone holding a backup can.
master-key:
	@if grep -q '^MASTER_KEY=' .env 2>/dev/null; then \
		echo "MASTER_KEY is in .env already"; \
	else \
		echo "MASTER_KEY=$$(openssl rand -base64 32)" >> .env && \
		echo "wrote a new MASTER_KEY to .env: keep a copy somewhere safe, apart from the database backups"; \
	fi

# HTTP API on localhost only (no authentication yet).
api:
	uv run $(ENV_FILE) uvicorn --factory app.api.main:create_app_from_env --host 127.0.0.1 --port 8000

# Background worker; reads REDIS_URL like the API does. SimpleWorker runs jobs
# in-process (no fork), which also works on macOS.
worker:
	uv run $(ENV_FILE) python -m app.worker

# Asks the ERP every BANK_WATCH_SECONDS for bank transfers it has not seen and
# matches the open invoices of the suppliers they went to (needs ERP_URL).
watch:
	uv run $(ENV_FILE) python -m app.watch

# Books every approved or auto-decided invoice, exactly once, every
# DELIVERY_SECONDS: in the ERP (ERP_URL; the mock ERP: make erp) and in the
# DATEV-style CSV booking lists (EXPORT_DIR) and a signed webhook
# (WEBHOOK_URL + WEBHOOK_SECRET), whichever are set.
deliver:
	uv run $(ENV_FILE) python -m app.deliver

# Reads every tenant's mailbox (its imap_* settings) every MAILBOX_SECONDS and
# ingests the attached invoices, once each (app/mailbox.py).
mailbox:
	uv run $(ENV_FILE) python -m app.mailbox

# The mailbox poller against a real IMAP server: GreenMail in Docker on
# localhost, synthetic mails only; the container is removed afterwards.
GREENMAIL = einvoice-greenmail
test-mailbox:
	docker container rm --force $(GREENMAIL) >/dev/null 2>&1 || true
	docker run --detach --name $(GREENMAIL) --publish 127.0.0.1:3143:3143 \
		--env GREENMAIL_OPTS="-Dgreenmail.setup.test.imap -Dgreenmail.hostname=0.0.0.0 -Dgreenmail.auth.disabled" \
		greenmail/standalone:2.1.5 >/dev/null
	for i in $$(seq 1 60); do uv run python -c "import imaplib; imaplib.IMAP4('127.0.0.1', 3143).logout()" 2>/dev/null && break; sleep 1; done
	GREENMAIL_IMAP=127.0.0.1:3143 uv run pytest tests/test_mailbox_imap.py \
		&& uv run python -m eval.mailbox --gate --imap 127.0.0.1:3143; status=$$?; \
		docker container rm --force $(GREENMAIL) >/dev/null; exit $$status

# The mailbox eval: 200 synthetic mails, read with crashes and restarts; every
# invoice once, everything else refused with the right reason, injection text
# in mails changes nothing. In memory; `make test-mailbox` runs it over IMAP.
eval-mailbox:
	uv run python -m eval.mailbox --gate

# Review UI (Next.js, in ui/). `make ui` expects the API from `make api`.
ui-install:
	pnpm --dir ui install --frozen-lockfile

ui:
	pnpm --dir ui dev

# Type-checks and builds the production bundle; fails, too, when a German text
# breaks the glossary or docs/german-texts.md is out of date.
ui-build: german-texts-check
	pnpm --dir ui build

# The list of every German text for a native speaker to read (docs/german-texts.md).
german-texts:
	pnpm --dir ui texts

german-texts-check:
	pnpm --dir ui texts:check

# Browser tests, in English and then in German (the German run also scans the
# rendered pages for English). They start their own in-memory API
# (tests/ui_server.py) and need a browser once: pnpm --dir ui exec playwright install chromium
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

# The credit-note set: 160 credit notes for world invoices (full and partial,
# with and without a reference, refunded, offset or not yet settled), as UBL,
# CII and text PDFs. Fails unless at least 95 % of the held-out credit notes,
# read correctly, are linked to the invoice they correct, and none is booked as
# an invoice or without review. No services, no model.
eval-credits:
	uv run python -m eval.credits --gate 0.95

# The decision engine's confidence threshold (needs the predictions of
# eval-pdf-llm and of the scan set, like calibrate). `decide-fit` rewrites
# app/decide/thresholds.json from the dev split; `eval-decision` reports the
# auto-rate vs. error curve on both splits, and the baseline extractor as a
# stress test of the gate. `eval-decision-pdf` is what CI can replay: text
# PDFs only; it warns when the auto-rate drops more than 5 points.
decide-fit:
	uv run python -m eval.sweep fit eval/out/pdf eval/out/scans

eval-decision:
	uv run python -m eval.sweep report eval/out/pdf eval/out/scans --gate
	uv run python -m eval.sweep report eval/out/pdf eval/out/scans --system baseline

eval-decision-pdf:
	uv run python -m eval.sweep report eval/out/pdf --gate

# Injection probes: 20 invoices with text that tries to steer the extraction
# model, each next to a control without it, through the application's own
# extraction, matching, checks and decision. Replays recorded model answers;
# fails if any injection changed a field, a route or a match.
# `eval-probes-record` calls the model for probes not recorded yet (~40 calls).
eval-probes:
	uv run python -m eval.probes --gate

eval-probes-record:
	uv run --env-file .env python -m eval.probes --llm-mode record

# Reading on the Belege sample (third-party synthetic images; not in git). Replays
# the answers recorded under data/private/belege/; BELEGE is the unpacked sample.
BELEGE ?= $(HOME)/Downloads/belege-sample-40
BELEGE_MODEL ?= claude-haiku-5-5
eval-belege:
	uv run python -m eval.belege $(BELEGE) --model $(BELEGE_MODEL)

# EVAL_PLAN §3: a local vision model (Ollama) instead of the hosted one, held-out
# split. Replays the recorded answers; to record again, start Ollama with the
# model pulled and use --llm-mode record. LOCAL_MODEL picks another ollama/… model.
eval-local:
	for set in pdf scans; do \
		uv run python -m eval.run eval/out/$$set --system local --llm-mode replay --split test || exit 1; \
		uv run python -m eval.run eval/out/$$set --system local-rules --llm-mode replay --split test || exit 1; \
		uv run python -m eval.score eval/out/$$set --system local-rules --split test || exit 1; \
	done
	uv run python -m eval.sweep report eval/out/pdf eval/out/scans --system local-rules --split test

# A local identity provider to try sign-in with (docs/sign-in-keycloak.md):
# Keycloak in dev mode on localhost:8180 with the test realm in deploy/keycloak.
keycloak:
	docker run -d --name einvoice-keycloak -p 127.0.0.1:8180:8080 \
		-e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=kc-test-admin \
		-v $(CURDIR)/deploy/keycloak/einvoice-realm.json:/opt/keycloak/data/import/einvoice-realm.json:ro \
		quay.io/keycloak/keycloak:26.4 start-dev --import-realm

keycloak-down:
	docker rm -f einvoice-keycloak
