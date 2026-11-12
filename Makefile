.PHONY: install lint format test check up migrate db-check test-pg test-integration eval-quick eval-full eval-pdf-baseline eval-pdf-llm eval-pdf-llm-record ocr-data eval-scans-baseline api worker

install:
	uv sync

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

# Vision-model extractor on the same PDFs, answered from stored recordings.
eval-pdf-llm:
	uv run python -m eval.generators.pdf --count 150 --out eval/out/pdf
	uv run python -m eval.run eval/out/pdf --system llm --llm-mode replay
	uv run python -m eval.score eval/out/pdf --system llm --split dev
	uv run python -m eval.score eval/out/pdf --system llm --split test

# Calls the model for every PDF without a recording. COSTS MONEY; needs credentials.
eval-pdf-llm-record:
	uv run python -m eval.generators.pdf --count 150 --out eval/out/pdf
	uv run python -m eval.run eval/out/pdf --system llm --llm-mode record

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
