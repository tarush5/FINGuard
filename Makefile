# FINGuard developer commands.
.DEFAULT_GOAL := help
SHELL := /bin/bash

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

install: ## Install backend and frontend dependencies
	cd backend && python -m pip install -r requirements-dev.txt
	cd frontend && npm ci

seed: ## Build the synthetic portfolio, score it and train the first models
	cd backend && python -m app.datagen.seed --reset

migrate: ## Apply database migrations
	cd backend && python -m alembic upgrade head

migration: ## Autogenerate a migration: make migration m="add x"
	cd backend && python -m alembic revision --autogenerate -m "$(m)"

api: ## Run the API with reload
	cd backend && python -m uvicorn app.main:app --reload --port 8000

web: ## Run the web app
	cd frontend && npm run dev

test: test-backend test-frontend ## Run every test suite

test-backend: ## Run backend tests
	cd backend && python -m pytest tests -q

test-frontend: ## Run frontend tests
	cd frontend && npm run test

smoke: ## Exercise every API endpoint against the seeded database
	cd backend && python -m scripts.smoke

bench: ## Measure decision latency percentiles
	cd backend && python -m scripts.benchmark

lint: ## Lint and type-check both sides
	cd backend && python -m ruff check app tests && python -m black --check app tests
	cd frontend && npm run lint && npm run typecheck

format: ## Auto-format the codebase
	cd backend && python -m black app tests && python -m ruff check --fix app tests
	cd frontend && npx prettier --write "src/**/*.{ts,tsx,css}"

up: ## Start the core stack in Docker
	docker compose --profile core up --build -d

up-full: ## Start core + streaming + ml
	docker compose --profile core --profile streaming --profile ml up --build -d

down: ## Stop the stack
	docker compose --profile core --profile streaming --profile ml --profile graph --profile orchestration down

clean: ## Remove local databases, artifacts and build output
	rm -f backend/*.db backend/*.db-wal backend/*.db-shm
	rm -rf backend/artifacts frontend/dist

.PHONY: help install seed migrate migration api web test test-backend test-frontend smoke bench lint format up up-full down clean
