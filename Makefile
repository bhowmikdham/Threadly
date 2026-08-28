.PHONY: up down build logs ps test compile psql clean e2e

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

# Full-cycle E2E against a FRESH stack (resets volumes: dev data is wiped).
e2e:
	docker compose down -v
	docker compose up -d --build
	docker compose exec -T gateway python - < tests/e2e/wait.py
	docker compose exec -T gateway python - < tests/e2e/smoke.py

psql:
	docker compose exec postgres psql -U threadly -d threadly

clean:
	docker compose down -v
