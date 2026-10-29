# Developer convenience targets.
.PHONY: help lint test test-integration db-test-up db-test-down migrate check seed mypy

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-18s %s\n", $$1, $$2}'

lint: ## Ruff lint the backend
	cd backend && ruff check app tests scripts

mypy: ## Type-check the backend application
	cd backend && mypy app

test: ## Run unit tests (no database needed)
	cd backend && ENCRYPTION_KEY=$${ENCRYPTION_KEY:-sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A=} pytest -m "not integration"

db-test-up: ## Start the ephemeral test Postgres+Timescale
	docker compose -f docker-compose.test.yml up -d --wait test-db

db-test-down: ## Tear down the test database
	docker compose -f docker-compose.test.yml down -v

test-integration: db-test-up ## Run the full suite incl. DB integration tests
	@test -n "$$TEST_DATABASE_URL" || { echo "TEST_DATABASE_URL is required" >&2; exit 1; }
	cd backend && \
	  ENCRYPTION_KEY=$${ENCRYPTION_KEY:-sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A=} \
	  TEST_DATABASE_URL="$$TEST_DATABASE_URL" \
	  pytest

check: db-test-up ## Migration drift check against a real database
	cd backend && \
	  POSTGRES_HOST=localhost POSTGRES_PORT=5433 POSTGRES_USER=test POSTGRES_PASSWORD=test \
	  POSTGRES_DB=qradar_obs_test ENCRYPTION_KEY=sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A= \
	  sh -c 'alembic upgrade head && alembic check'

migrate: ## Apply migrations to the configured database
	cd backend && alembic upgrade head

seed: ## Seed roles + inventory via the configured provider
	cd backend && python -m app.services.seed
