.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test test-all migrate revision api worker beat up down logs

help: ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-12s %s\n", $$1, $$2}'

install: ## Create the venv and install hooks
	uv sync
	uv run pre-commit install

lint: ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

format: ## Auto-fix lint and formatting
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## mypy (strict)
	uv run mypy src tests

test: ## Unit tests (no services needed)
	uv run pytest

test-all: ## Unit + integration tests against the compose database
	TEST_DATABASE_URL=postgresql+asyncpg://evidentia:evidentia@localhost:5432/evidentia uv run pytest --cov

migrate: ## Apply migrations
	uv run alembic upgrade head

revision: ## New migration: make revision m="add something"
	uv run alembic revision --autogenerate -m "$(m)"

api: ## Run the API with reload
	uv run uvicorn --factory evidentia.main:create_app --reload --port 8000

worker: ## Run a Celery worker on every queue
	uv run celery -A evidentia.workers.celery_app worker -Q default,documents,knowledge,generation,integrations --loglevel=INFO

beat: ## Run the Celery scheduler
	uv run celery -A evidentia.workers.celery_app beat --loglevel=INFO

up: ## Start the full stack
	docker compose up -d --build

down: ## Stop the stack
	docker compose down

logs: ## Tail application logs
	docker compose logs -f api worker
