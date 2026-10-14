"""Run the E2E API with an isolated database, removed after graceful shutdown."""

import os
import signal
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

ROOT = Path(__file__).resolve().parents[2]


def exit_cleanly(_signum, _frame):
    # Uvicorn re-raises SIGTERM after shutdown; unwind the temporary-directory context.
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, exit_cleanly)
    with TemporaryDirectory(prefix="evalops-e2e-") as directory:
        os.environ["EVALOPS_DATABASE_URL"] = f"sqlite:///{Path(directory) / 'evalops.db'}"
        os.environ["EVALOPS_AUTO_MIGRATE"] = "true"
        os.environ["EVALOPS_ALLOWED_HOSTS"] = "localhost,127.0.0.1"
        os.environ["EVALOPS_CORS_ORIGINS"] = (
            f"http://127.0.0.1:{os.environ.get('EVALOPS_E2E_WEB_PORT', '5174')}"
        )
        uvicorn.run(
            "evalops.main:app",
            app_dir=str(ROOT / "backend"),
            host="127.0.0.1",
            port=int(os.environ.get("EVALOPS_E2E_API_PORT", "8001")),
            log_level="warning",
        )
