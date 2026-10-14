# Browser end-to-end tests

The Playwright suite exercises the React UI against the real FastAPI API and
SQLite database in Chromium. It covers dashboard activity, datasets and knowledge
base passages, run history and result details, recorded response submission,
baseline/candidate comparisons, ready and blocked release reports, Markdown
export, empty states, missing resources, validation failures, and error recovery.
Only the network-failure test intercepts a request; all other API responses are real.

## Run locally

Use Python 3.13 and Node 22, matching CI:

```sh
make setup
make e2e-install
make e2e
```

`make e2e-install` downloads Playwright's matching Chromium build, not an AI model.
On Linux, install browser system dependencies once with
`cd frontend && npx playwright install --with-deps chromium`.
Tests need no API keys, paid services, MLX, or local model downloads.

Playwright starts and stops both servers automatically. The API listens on
127.0.0.1:8001; Vite listens on 127.0.0.1:5174. Both refuse to reuse an existing
server. The Python launcher overrides database settings with a unique temporary
SQLite database, applies the real Alembic migrations, and removes the database
on graceful shutdown. It never opens or deletes the developer's database.
Set `EVALOPS_E2E_API_PORT` and `EVALOPS_E2E_WEB_PORT` to unused distinct ports
if those defaults are occupied.

The setup project first verifies empty screens, then imports the existing Kestrel
support and RAG fixtures through the public API. Recorded responses and saved
judge verdicts are replayed without inference. Tests never rewrite fixtures,
human-review labels, scoring policy, or release-gate thresholds. One worker and
zero retries make failures visible and avoid shared-state races. A temporary run
created by the upload test is deleted through the API afterward.

## Diagnose and verify

From `frontend/`:

```sh
npm run typecheck:e2e
npm run test:e2e -- --project=chromium
npx playwright show-report
```

Selecting the Chromium project also runs its seed dependency. Do not use
`--no-deps` on a fresh database. The HTML report is in `frontend/playwright-report/`;
failure screenshots and traces are in `frontend/test-results/`. Both are ignored
by Git. To inspect a trace, use `npx playwright show-trace path/to/trace.zip`.
`npm test` continues to run only Vitest unit tests.

## GitHub Actions

The E2E job installs the hash-locked backend and uses `npm ci`, caches Chromium
by runner OS/architecture and the npm lockfile hash, and always installs Linux
system dependencies (including on cache hits). It runs the same setup and browser
tests, then uploads HTML reports and failure traces for seven days. A test failure
fails CI. No deployment, publishing, or evaluation-policy changes are included.
