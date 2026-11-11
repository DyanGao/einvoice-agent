.PHONY: install lint format test check up migrate db-check test-pg

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
