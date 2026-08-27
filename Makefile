.PHONY: up down build logs ps test compile psql clean

up:
	docker compose up -d --build

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

# Pure-logic unit tests (planner, state machine, cursor, extractor, PII).
test:
	python3 -m venv .venv-test 2>/dev/null || true
	.venv-test/bin/pip install -q pydantic pytest
	.venv-test/bin/python -m pytest tests/ -q

compile:
	python3 -m compileall -q libs services tests

psql:
	docker compose exec postgres psql -U threadly -d threadly

clean:
	docker compose down -v
