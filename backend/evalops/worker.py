"""Command-line entrypoint for the independent evaluation-job worker.

Run from the backend directory with ``.venv/bin/python -m evalops.worker``.
The worker never applies migrations; upgrade the configured database explicitly first.
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading

from sqlalchemy import text

from .config import Settings
from .db import make_engine, make_session_factory
from .workers.evaluation import EvaluationJobWorker


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local EvalOps evaluation worker")
    parser.add_argument("--once", action="store_true", help="claim at most one available job")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--owner", default=None, help="optional worker identity prefix")
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be greater than zero")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception as exc:
        engine.dispose()
        raise SystemExit(
            "EvalOps database schema is unavailable; apply migrations through revision 0013 first."
        ) from exc
    if revision not in {"0012", "0013"}:
        engine.dispose()
        raise SystemExit(
            f"EvalOps database schema is at {revision!r}; upgrade it to revision 0012 or 0013 "
            "before starting the worker."
        )
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda _signum, _frame: stop.set())
    worker = EvaluationJobWorker(
        make_session_factory(engine),
        worker_id=args.owner,
        max_queued_per_workspace=settings.max_queued_jobs_per_workspace,
        max_active_per_workspace=settings.max_active_jobs_per_workspace,
    )
    try:
        if args.once:
            worker.run_once()
        else:
            worker.serve(stop, poll_seconds=args.poll_seconds)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
