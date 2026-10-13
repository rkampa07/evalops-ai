# EvalOps AI — local developer workflows. Everything stays inside this directory.
PY      := backend/.venv/bin/python
PIP     := backend/.venv/bin/pip
API_URL ?= http://127.0.0.1:8000

.PHONY: setup setup-local-ai backend frontend seed migrate test test-backend test-frontend lint check clean-db \
	rag-demo rag-demo-live rag-generate rag-calibrate

GEN_MODEL   ?= mlx-community/Qwen2.5-1.5B-Instruct-4bit
JUDGE_MODEL ?= mlx-community/Llama-3.2-3B-Instruct-4bit
RAG         := examples/rag

setup:            ## Create the venv and install backend + frontend dependencies
	test -d backend/.venv || python3 -m venv backend/.venv
	$(PIP) install -q -e ".[dev]"
	cd frontend && npm install --no-fund --no-audit

setup-local-ai:   ## Optional: MLX for local generation/judging (models are downloaded separately)
	$(PIP) install -q -e ".[dev,local-ai]"

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
