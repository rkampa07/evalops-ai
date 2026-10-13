# EvalOps AI — local developer workflows. Everything stays inside this directory.
PY      := backend/.venv/bin/python
PIP     := backend/.venv/bin/pip
API_URL ?= http://127.0.0.1:8000

.PHONY: setup backend frontend seed migrate test test-backend test-frontend lint check clean-db

setup:            ## Create the venv and install backend + frontend dependencies
	test -d backend/.venv || python3 -m venv backend/.venv
	$(PIP) install -q -e ".[dev]"
	cd frontend && npm install --no-fund --no-audit

backend:          ## Run the API on http://127.0.0.1:8000 (auto-migrates)
	cd backend && .venv/bin/uvicorn evalops.main:app --host 127.0.0.1 --port 8000 --reload

frontend:         ## Run the UI on http://127.0.0.1:5173
	cd frontend && npm run dev

seed:             ## Load the fictional Kestrel demo through the API (API must be running)
	$(PY) backend/scripts/seed_demo.py --base-url $(API_URL)

migrate:          ## Apply database migrations explicitly
	cd backend && PYTHONPATH=. .venv/bin/alembic upgrade head

test-backend:
	backend/.venv/bin/pytest

test-frontend:
	cd frontend && npx vitest run

test: test-backend test-frontend

lint:
	backend/.venv/bin/ruff check backend tests
	backend/.venv/bin/ruff format --check backend tests
	cd frontend && npx tsc && npx eslint .

check: lint test  ## Everything CI would run

clean-db:         ## Delete the local SQLite database (backend/data/evalops.db only)
	rm -f backend/data/evalops.db
