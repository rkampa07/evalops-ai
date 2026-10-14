# CI and dependency locking

GitHub Actions runs on pull requests, pushes to `main`, and manual dispatch.
Independent Ubuntu jobs check the backend (Ruff lint/format and all pytest suites)
and frontend (TypeScript, ESLint, Vitest, and production build). Failed checks fail
the workflow. Repository branch protection can require these jobs before merging.
The workflow does not publish or deploy artifacts, or change evaluation release gates.

A third job runs Playwright against an isolated API and UI in Chromium, with browser
caching and report uploads. See [Browser end-to-end tests](e2e.md) for setup and coverage.

CI uses Python 3.13 and Node 22. Backend tests use deterministic scoring and recorded
fixtures; no MLX install, model downloads, API keys, or paid services are required.

## Reproduce locally

With Python 3.13 and Node 22 available, run `make setup`, then `make check`.
Frontend installation uses `npm ci` and the committed `frontend/package-lock.json`.
Python installation verifies hashes in `requirements/tooling.lock` and
`requirements/dev.lock`, then installs the project with `--no-deps
--no-build-isolation` so editable build dependencies are also locked.

`requirements/dev.lock` includes the backend and test dependencies.
`requirements/local-ai.lock` adds optional MLX dependencies for Apple Silicon;
CI never installs it. `make setup-local-ai` is optional and follows `make setup`.
Model downloads remain a separate opt-in operation.

## Update dependencies

Edit `pyproject.toml` (or `requirements/tooling.in` for compiler/build tooling),
then run `make lock` in the Python 3.13 environment created by `make setup`.
The compiler version is pinned in `requirements/tooling.in`. Commit regenerated
lockfiles together with the dependency declaration changes and run `make setup`
and `make check`. To deliberately upgrade existing pins, invoke the corresponding
`pip-compile` commands in the Makefile with `--upgrade` (or `--upgrade-package`).
Hash checking rejects unexpected package contents; it does not replace dependency
review. The optional MLX lock is intended for Apple Silicon, not Ubuntu CI.
