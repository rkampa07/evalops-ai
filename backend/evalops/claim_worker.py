"""CLI for the independent semantic claim assessment worker; no startup model loading."""

from __future__ import annotations

import argparse
import logging
import signal
import threading

from sqlalchemy import text

from .config import Settings
from .db import make_engine, make_session_factory
from .workers.claim_assessment import ClaimAssessmentWorker


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the EvalOps claim assessment worker")
    parser.add_argument("--once", action="store_true", help="claim at most one assessment")
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
            "EvalOps schema is unavailable; apply migrations through revision 0014 first."
        ) from exc
    if revision != "0014":
        engine.dispose()
        raise SystemExit(f"EvalOps schema is at {revision!r}; upgrade it to revision 0014 first.")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda _signum, _frame: stop.set())
    worker = ClaimAssessmentWorker(
        make_session_factory(engine),
        worker_id=args.owner,
        max_queued_per_workspace=settings.max_queued_citation_assessments_per_workspace,
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
