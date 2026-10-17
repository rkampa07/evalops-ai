"""Run the E2E API with an isolated database, removed after graceful shutdown."""

import os
import signal
import sys
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def exit_cleanly(_signum, _frame):
    # Uvicorn re-raises SIGTERM after shutdown; unwind the temporary-directory context.
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, exit_cleanly)
    with TemporaryDirectory(prefix="evalops-e2e-") as directory:
        db_url = f"sqlite:///{Path(directory) / 'evalops.db'}"
        os.environ["EVALOPS_DATABASE_URL"] = db_url
        os.environ["EVALOPS_AUTO_MIGRATE"] = "true"
        os.environ["EVALOPS_ENV"] = "development"
        os.environ["EVALOPS_AUTH_MODE"] = "development"
        os.environ["EVALOPS_ALLOWED_HOSTS"] = "localhost,127.0.0.1"
        # Deterministic stand-in for live chat: browser tests never load a real model.
        os.environ["EVALOPS_CHAT_BACKEND"] = "fixture"
        os.environ["EVALOPS_CORS_ORIGINS"] = (
            f"http://127.0.0.1:{os.environ.get('EVALOPS_E2E_WEB_PORT', '5174')}"
        )

        from evalops.db import make_engine, make_session_factory, run_migrations
        from evalops.workers.evaluation import EvaluationJobWorker

        run_migrations(db_url)
        worker_engine = make_engine(db_url)
        stop_event = threading.Event()
        worker = EvaluationJobWorker(
            make_session_factory(worker_engine),
            worker_id="e2e-worker",
        )
        worker_thread = threading.Thread(
            target=worker.serve,
            args=(stop_event,),
            kwargs={"poll_seconds": 0.1},
            daemon=True,
        )
        worker_thread.start()
        try:
            uvicorn.run(
                "evalops.main:app",
                app_dir=str(ROOT / "backend"),
                host="127.0.0.1",
                port=int(os.environ.get("EVALOPS_E2E_API_PORT", "8001")),
                log_level="warning",
            )
        finally:
            stop_event.set()
            worker_thread.join(timeout=2.0)
            worker_engine.dispose()
