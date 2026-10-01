# one entry point for every step: `make all` runs checks, rebuilds the data, scores it, and opens the dashboard
DATE ?= 2026-09-18
PORT ?= 8501
DATA ?= data/transactions.csv.gz

.DEFAULT_GOAL := help
.PHONY: help all install lint types test check data ingest report dashboard up down logs clean

help: ## list the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

all: install check data ingest report dashboard ## everything, locally: checks, data, scoring, report, then the dashboard

install: ## install dependencies with uv
	uv sync

lint: ## ruff lint and format check
	uv run ruff check .
	uv run ruff format --check .

types: ## ty type check
	uv run ty check

test: ## pytest
	uv run pytest

check: lint types test ## lint, types, and tests

data: ## rebuild the seeded dataset from config.toml
	uv run fraud-intel generate

ingest: ## load DATA, replay the stream, save scores and alerts
	uv run fraud-intel ingest --file $(DATA) --quiet

report: ## export the daily Top 50 for DATE to reports/
	uv run fraud-intel report --date $(DATE)

dashboard: ## run the dashboard locally on PORT
	@echo "dashboard: http://localhost:$(PORT)"
	uv run streamlit run src/fraud_intel/dashboard/app.py --server.port=$(PORT) --server.headless=true

up: ## build and start the dashboard in Docker on port 8501
	docker compose up --build -d
	@echo "dashboard: http://localhost:8501"

down: ## stop the Docker stack
	docker compose down

logs: ## follow the container logs, including printed alerts
	docker compose logs -f dashboard

clean: ## remove the database, reports, and caches (the committed dataset stays)
	rm -rf data/*.duckdb data/*.duckdb.wal reports .pytest_cache .ruff_cache
