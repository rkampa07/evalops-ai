# EvalOps AI — local developer workflows. Everything stays inside this directory.
PY      := backend/.venv/bin/python
PIP     := backend/.venv/bin/pip
API_URL ?= http://127.0.0.1:8000

.PHONY: setup setup-local-ai backend frontend seed migrate test test-backend test-frontend lint check clean-db \
	rag-demo rag-demo-live rag-generate rag-calibrate labels-export labels-import lock e2e e2e-install

GEN_MODEL   ?= mlx-community/Qwen2.5-1.5B-Instruct-4bit
JUDGE_MODEL ?= mlx-community/Llama-3.2-3B-Instruct-4bit
RAG         := examples/rag

setup:            ## Create the venv and install backend + frontend dependencies
	test -d backend/.venv || python3 -m venv backend/.venv
	$(PIP) install -q --require-hashes -r requirements/tooling.lock
	$(PIP) install -q --require-hashes -r requirements/dev.lock
	$(PIP) install -q --no-deps --no-build-isolation -e .
	cd frontend && npm ci --no-fund --no-audit

setup-local-ai:   ## Optional: MLX for local generation/judging (models are downloaded separately)
	$(PIP) install -q --require-hashes -r requirements/local-ai.lock
	$(PIP) install -q --no-deps --no-build-isolation -e .

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

check: lint test  ## Lint and tests used by CI
	cd frontend && npm run build

clean-db:         ## Delete the local SQLite database (backend/data/evalops.db only)
	rm -f backend/data/evalops.db

# ---------------------------------------------------------------- RAG (Phase 3)

rag-demo:         ## Replay recorded local-model answers + judge verdicts (no model needed; API running)
	$(PY) backend/scripts/rag_demo.py --base-url $(API_URL)

rag-demo-live:    ## Same, but run the judge model live (needs setup-local-ai + downloaded judge model)
	$(PY) backend/scripts/rag_demo.py --base-url $(API_URL) --judge live

rag-generate:     ## Regenerate answers for both prompts with the local model, one at a time, measured
	for v in rag-v1 rag-v2; do \
		/usr/bin/time -l $(PY) backend/scripts/rag_generate.py --prompt-version $$v --model $(GEN_MODEL) \
			--out $(RAG)/responses/qwen2.5-1.5b_$$v.json 2> $(RAG)/benchmarks/time_qwen2.5-1.5b_$$v.txt; \
	done

rag-calibrate:    ## Measure heuristic + judge agreement with the labelled claims
	/usr/bin/time -l $(PY) backend/scripts/rag_judge.py calibrate --model $(JUDGE_MODEL) \
		--out $(RAG)/calibration/llama3.2-3b_judge-v2.json 2> $(RAG)/benchmarks/time_llama3.2-3b_calibrate_v2.txt

labels-export:    ## Write examples/rag/labels/claims_review.csv for review in Excel / Google Sheets
	$(PY) backend/scripts/labels_csv.py export

labels-import:    ## Apply rows marked Reviewed: make labels-import REVIEWER="Your Name" [CSV=path] [DRY=1]
	@test -n "$(REVIEWER)" || (echo 'usage: make labels-import REVIEWER="Your Name" [CSV=path] [DRY=1]'; exit 2)
	$(PY) backend/scripts/labels_csv.py $(if $(CSV),--csv "$(CSV)") import --reviewer "$(REVIEWER)" $(if $(DRY),--dry-run)

lock:             ## Regenerate hash-pinned lockfiles using Python 3.13
	$(PY) -c 'import sys; assert sys.version_info[:2] == (3, 13), "Lock generation requires Python 3.13"'
	backend/.venv/bin/pip-compile -q --generate-hashes --allow-unsafe --strip-extras \
		--output-file requirements/tooling.lock requirements/tooling.in
	backend/.venv/bin/pip-compile -q --generate-hashes --strip-extras --extra dev \
		--output-file requirements/dev.lock pyproject.toml
	backend/.venv/bin/pip-compile -q --generate-hashes --strip-extras --extra dev --extra local-ai \
		--output-file requirements/local-ai.lock pyproject.toml

e2e:              ## Playwright browser tests against a real API + UI on ports 8001/5174 (temp database)
	cd frontend && npm run test:e2e

e2e-install:      ## Install the Chromium version used by Playwright
	cd frontend && npm run test:e2e:install
