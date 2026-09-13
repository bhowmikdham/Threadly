COMPOSE=docker compose
DEV=$(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: help dev up down logs ps test lint fmt db-revision db-upgrade psql

help: ## list targets
	@grep -E "^[a-zA-Z_-]+:.*?## " Makefile | awk -F":.*?## " "{printf \"%-14s %s\\n\", \$$1, \$$2}"

dev: ## local stack: api hot-reload on :8000 + postgres + chroma (no caddy)
	$(DEV) up --build api postgres chroma

up: ## prod-shaped stack, detached (caddy TLS on :443)
	$(COMPOSE) up -d --build

down: ## stop everything (volumes survive)
	$(COMPOSE) down

logs: ## tail all service logs
	$(COMPOSE) logs -f --tail=100

ps: ## service status
	$(COMPOSE) ps

test: ## run backend tests
	cd backend && python -m pytest -q

lint: ## ruff check
	cd backend && python -m ruff check app tests

fmt: ## ruff format
	cd backend && python -m ruff format app tests

db-revision: ## autogenerate migration from model changes: make db-revision m="msg"
	$(DEV) run --rm api alembic revision --autogenerate -m "$(m)"

db-upgrade: ## apply migrations
	$(DEV) run --rm api alembic upgrade head

psql: ## psql into the dev database
	$(DEV) exec postgres psql -U threadly -d threadly
