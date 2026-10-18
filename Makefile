# EvalOps AI — local developer workflows. Everything stays inside this directory.
PY      := backend/.venv/bin/python
PIP     := backend/.venv/bin/pip
API_URL ?= http://127.0.0.1:8000

.PHONY: setup setup-local-ai backend chat chat-fixture frontend seed migrate test test-backend test-frontend lint check clean-db \
	rag-demo rag-demo-live rag-generate rag-calibrate labels-export labels-import lock e2e e2e-install \
	assessment-demo-backend assessment-demo-seed assessment-demo-worker assessment-demo-worker-real \
	setup-nli assessment-nli-download assessment-nli-check assessment-nli-smoke clean-assessment-demo-db

GEN_MODEL   ?= mlx-community/Qwen2.5-1.5B-Instruct-4bit
JUDGE_MODEL ?= mlx-community/Llama-3.2-3B-Instruct-4bit
RAG         := examples/rag
# Live generation/judging writes here (git-ignored); committed recordings are never overwritten.
LIVE        := $(RAG)/live
ASSESS_DEMO_DB     := $(CURDIR)/backend/data/assessment_demo.db
ASSESS_DEMO_DB_URL := sqlite:///$(ASSESS_DEMO_DB)

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
	cd backend && EVALOPS_ENV=$${EVALOPS_ENV:-development} EVALOPS_AUTH_MODE=$${EVALOPS_AUTH_MODE:-development} .venv/bin/uvicorn evalops.main:app --host 127.0.0.1 --port 8000 --reload

chat-fixture:     ## API with live chat using the deterministic fixture backend (no model)
	cd backend && EVALOPS_ENV=$${EVALOPS_ENV:-development} EVALOPS_AUTH_MODE=$${EVALOPS_AUTH_MODE:-development} EVALOPS_CHAT_BACKEND=fixture .venv/bin/uvicorn evalops.main:app --host 127.0.0.1 --port 8000 --reload

chat:             ## API with live chat on the local MLX model (needs setup-local-ai + downloaded model)
	cd backend && EVALOPS_ENV=$${EVALOPS_ENV:-development} EVALOPS_AUTH_MODE=$${EVALOPS_AUTH_MODE:-development} EVALOPS_CHAT_BACKEND=mlx .venv/bin/uvicorn evalops.main:app --host 127.0.0.1 --port 8000

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
	mkdir -p $(LIVE)/responses $(LIVE)/benchmarks
	for v in rag-v1 rag-v2 rag-v3; do \
		/usr/bin/time -l $(PY) backend/scripts/rag_generate.py --prompt-version $$v --model $(GEN_MODEL) \
			--out $(LIVE)/responses/qwen2.5-1.5b_$$v.json 2> $(LIVE)/benchmarks/time_qwen2.5-1.5b_$$v.txt; \
	done

rag-calibrate:    ## Measure heuristic + judge agreement with the labelled claims
	mkdir -p $(LIVE)/calibration $(LIVE)/benchmarks
	/usr/bin/time -l $(PY) backend/scripts/rag_judge.py calibrate --model $(JUDGE_MODEL) \
		--out $(LIVE)/calibration/llama3.2-3b_judge-v2.json 2> $(LIVE)/benchmarks/time_llama3.2-3b_calibrate_v2.txt

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

# ---------------------------------------------------------------- Assessment Demo (Phase 5F)

assessment-demo-backend: ## Run the API on http://127.0.0.1:8000 using isolated assessment_demo.db
	cd backend && EVALOPS_DATABASE_URL="$(ASSESS_DEMO_DB_URL)" EVALOPS_ENV=development EVALOPS_AUTH_MODE=development EVALOPS_API_JOB_SUBMISSIONS_RPM=60 .venv/bin/uvicorn evalops.main:app --host 127.0.0.1 --port 8000

assessment-demo-seed:    ## Seed RAG dataset and recorded runs into the running demo API (no model needed)
	$(PY) backend/scripts/rag_demo.py --base-url $(API_URL)

assessment-demo-worker:  ## Run frozen citation worker + simulated claim worker on isolated assessment_demo.db
	EVALOPS_DATABASE_URL="$(ASSESS_DEMO_DB_URL)" $(PY) backend/scripts/assessment_demo_worker.py --verifier simulated

setup-nli: ## Optional pinned CPU NLI packages only (macOS arm64, Python 3.13; no MLX)
	$(PIP) install --require-hashes -r requirements/nli.lock

assessment-nli-download: ## Explicitly fetch only the exact pinned model/config/tokenizer files
	$(PY) backend/scripts/assessment_nli.py download

assessment-nli-check: ## Verify local NLI dependencies/snapshot without model loading
	$(PY) backend/scripts/assessment_nli.py check

assessment-nli-smoke: ## Run actual CPU entailment/contradiction/neutral inference, offline
	HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false $(PY) backend/scripts/assessment_nli.py smoke

assessment-demo-worker-real: ## Run the real pinned CPU claim verifier on isolated assessment_demo.db
	EVALOPS_DATABASE_URL="$(ASSESS_DEMO_DB_URL)" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false $(PY) backend/scripts/assessment_demo_worker.py --verifier real

clean-assessment-demo-db: ## Safely remove only backend/data/assessment_demo.db and sidecars when idle
	EVALOPS_DATABASE_URL="$(ASSESS_DEMO_DB_URL)" $(PY) backend/scripts/assessment_demo_worker.py --clean-db
