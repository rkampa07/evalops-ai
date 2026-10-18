"""Isolated local assessment demo worker and cleanup helper.

Runs CitationAssessmentWorker (real frozen citation-evaluator-v4) and ClaimAssessmentWorker
(real pinned CPU NLI by default; explicitly select --verifier simulated for a model-free stub)
against a dedicated demo SQLite database (default: backend/data/assessment_demo.db).
Refuses to touch backend/data/evalops.db.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from evalops.claim_assessments import config_digest
from evalops.config import BACKEND_DIR
from evalops.db import make_engine, make_session_factory, run_migrations
from evalops.models import ClaimAssessmentJob
from evalops.nli_runtime import check_local_nli, make_cpu_verifier
from evalops.semantic.nli import SemanticBackendUnavailable
from evalops.semantic.segments import evidence_units
from evalops.semantic.verdict import (
    CONTRADICTED,
    INSUFFICIENT,
    SUPPORTED,
    SemanticVerdict,
)
from evalops.workers.citation_assessment import CitationAssessmentWorker
from evalops.workers.claim_assessment import ClaimAssessmentWorker

DEFAULT_DEMO_DB_PATH = (BACKEND_DIR / "data" / "assessment_demo.db").resolve()
LIVE_DB_PATH = (BACKEND_DIR / "data" / "evalops.db").resolve()
DEMO_SIDECAR_SUFFIXES = ("", "-wal", "-shm", "-journal")
_WORD_RE = re.compile(r"[a-z0-9]+")
_NEGATION_TOKENS = frozenset({"not", "never", "no", "cannot", "nonrefundable", "ineligible"})


def _resolve_sqlite_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise ValueError(f"Demo worker requires a local sqlite:/// URL, got: {database_url!r}")
    raw_path = database_url.removeprefix("sqlite:///")
    if not raw_path or raw_path == ":memory:":
        raise ValueError("Demo worker requires a file-backed sqlite:/// path, not :memory:.")
    resolved = Path(raw_path).resolve()
    if resolved == LIVE_DB_PATH:
        raise ValueError(
            f"Refusing to use live database {LIVE_DB_PATH}. "
            f"Use an isolated demo database such as sqlite:///{DEFAULT_DEMO_DB_PATH}."
        )
    return resolved


def _tokens(text: str) -> set[str]:
    return {tok for tok in _WORD_RE.findall(text.lower()) if len(tok) > 2}


class SimulatedDemoClaimVerifier:
    """Deterministic lexical stand-in for ClaimAssessmentWorker; never loads DeBERTa weights."""

    SIMULATED_NOTICE = (
        "[SIMULATED DEMO VERIFIER — deterministic lexical stand-in; no DeBERTa NLI model loaded]"
    )

    def __init__(self) -> None:
        self._digest = config_digest()

    def verify(self, question: str, claim: str, evidence: Mapping[str, str]) -> SemanticVerdict:
        units = evidence_units(evidence)
        claim_tokens = _tokens(claim)
        best_unit: tuple[str, str, str] | None = None
        best_overlap = 0.0

        for passage_id, unit_text, scope in units:
            unit_tokens = _tokens(unit_text)
            if not claim_tokens or not unit_tokens:
                continue
            overlap = len(claim_tokens & unit_tokens) / len(claim_tokens)
            if overlap > best_overlap:
                best_overlap = overlap
                best_unit = (passage_id, unit_text, scope)

        if best_unit is None or best_overlap < 0.35:
            return SemanticVerdict(
                label=INSUFFICIENT,
                reason=(
                    f"{self.SIMULATED_NOTICE} Retrieved passages do not share sufficient "
                    "deterministic token overlap with this claim."
                ),
                deciding=[],
                guards=["simulated_demo_verifier"],
                model={
                    "backend": "simulated_demo_stub",
                    "model_id": "demo-stub/simulated-lexical-verifier",
                    "revision": None,
                },
                config={
                    "question_pass": "not needed",
                    "digest": self._digest,
                    "simulated_demo": True,
                },
            )

        passage_id, unit_text, scope = best_unit
        claim_negated = bool(_tokens_with_negation(claim) & _NEGATION_TOKENS)
        unit_negated = bool(_tokens_with_negation(unit_text) & _NEGATION_TOKENS)

        if claim_negated != unit_negated and best_overlap >= 0.45:
            label = CONTRADICTED
            relation = "contradict"
            detail = f"Polarity mismatch against {passage_id} (token overlap {best_overlap:.0%})."
        else:
            label = SUPPORTED
            relation = "support"
            detail = (
                f"Matched retrieved evidence in {passage_id} (token overlap {best_overlap:.0%})."
            )

        return SemanticVerdict(
            label=label,
            reason=f"{self.SIMULATED_NOTICE} {detail}",
            deciding=[
                {
                    "passage_id": passage_id,
                    "relation": relation,
                    "scope": scope,
                    "unit": unit_text,
                }
            ],
            guards=["simulated_demo_verifier"],
            model={
                "backend": "simulated_demo_stub",
                "model_id": "demo-stub/simulated-lexical-verifier",
                "revision": None,
            },
            config={
                "question_pass": "not needed",
                "digest": self._digest,
                "simulated_demo": True,
            },
            uncalibrated_scores=[
                {
                    "passage_id": passage_id,
                    "scope": scope,
                    "simulated_overlap": round(best_overlap, 4),
                }
            ],
        )


def _tokens_with_negation(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _active_processes_for_db(db_path: Path) -> list[str]:
    """Return active OS process descriptions referencing db_path (excluding current process)."""
    my_pid = str(os.getpid())
    active: list[str] = []

    try:
        lsof = subprocess.run(
            ["lsof", "-F", "pc", "--", str(db_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if lsof.returncode == 0 and lsof.stdout.strip():
            current_pid = ""
            for line in lsof.stdout.splitlines():
                if line.startswith("p"):
                    current_pid = line[1:]
                elif line.startswith("c") and current_pid and current_pid != my_pid:
                    active.append(f"pid {current_pid} ({line[1:]})")
    except FileNotFoundError:
        pass

    try:
        ps = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            check=False,
        )
        if ps.returncode == 0:
            db_name = db_path.name
            for raw in ps.stdout.splitlines():
                parts = raw.strip().split(None, 1)
                if len(parts) != 2:
                    continue
                pid, cmd = parts
                if pid == my_pid:
                    continue
                if db_name in cmd or "assessment_demo_worker.py" in cmd:
                    entry = f"pid {pid} ({cmd[:80]})"
                    if entry not in active:
                        active.append(entry)
    except FileNotFoundError:
        pass

    return active


def clean_demo_database(db_path: Path) -> int:
    if db_path == LIVE_DB_PATH or db_path != DEFAULT_DEMO_DB_PATH:
        print(
            f"error: refusing to clean non-designated database path: {db_path}\n"
            f"Only {DEFAULT_DEMO_DB_PATH} may be cleaned by this command.",
            file=sys.stderr,
        )
        return 2

    active = _active_processes_for_db(db_path)
    if active:
        print(
            f"error: refusing to delete {db_path} while demo processes are active:\n"
            + "\n".join(f"  - {item}" for item in active)
            + "\nStop the demo backend and worker first (Ctrl+C), then re-run cleanup.",
            file=sys.stderr,
        )
        return 1

    removed: list[Path] = []
    for suffix in DEMO_SIDECAR_SUFFIXES:
        target = Path(f"{db_path}{suffix}")
        if target.resolve() == LIVE_DB_PATH:
            raise RuntimeError("Safety invariant violated: target resolved to live evalops.db")
        if target.exists():
            target.unlink()
            removed.append(target)

    if removed:
        print("Removed isolated assessment demo database files:")
        for item in removed:
            print(f"  - {item}")
    else:
        print(f"No demo database files found at {db_path} (already clean).")
    return 0


def _claim_exit_status(session_factory, claim_id: int | None) -> int:
    if claim_id is None:
        return 0
    with session_factory() as session:
        job = session.get(ClaimAssessmentJob, claim_id)
        if job and job.status == "failed":
            print(
                f"Claim assessment #{claim_id} failed: {job.error_code}. No fallback.",
                file=sys.stderr,
            )
            return 1
    return 0


def main(argv=None) -> int:
    default_url = os.environ.get("EVALOPS_DATABASE_URL", f"sqlite:///{DEFAULT_DEMO_DB_PATH}")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--database-url",
        default=default_url,
        help="Isolated SQLite database URL (default: EVALOPS_DATABASE_URL or assessment_demo.db).",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=0.4,
        help="Polling interval in seconds when idle (default: 0.4).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one citation job and one claim job, then exit.",
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="Run Alembic migrations on the isolated demo database before starting.",
    )
    parser.add_argument(
        "--clean-db",
        action="store_true",
        help="Delete the designated demo database and sidecars if no demo processes are active.",
    )
    parser.add_argument(
        "--verifier",
        choices=("real", "simulated"),
        default="real",
        help="Claim verifier: pinned CPU NLI (default), or explicit simulated model-free demo.",
    )
    args = parser.parse_args(argv)

    try:
        db_path = _resolve_sqlite_path(args.database_url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.clean_db:
        return clean_demo_database(db_path)

    if args.verifier == "real":
        try:
            check_local_nli()
        except (ImportError, OSError, SemanticBackendUnavailable) as exc:
            print(f"Real NLI unavailable: {exc}. No simulated fallback.", file=sys.stderr)
            return 2

    database_url = f"sqlite:///{db_path}"
    if args.migrate or not db_path.exists():
        run_migrations(database_url)

    engine = make_engine(database_url)
    session_factory = make_session_factory(engine)

    citation_worker = CitationAssessmentWorker(session_factory)
    claim_worker = ClaimAssessmentWorker(
        session_factory,
        verifier_factory=(
            make_cpu_verifier if args.verifier == "real" else SimulatedDemoClaimVerifier
        ),
    )

    if args.once:
        try:
            citation_id = citation_worker.run_once()
            claim_id = claim_worker.run_once()
            print(
                f"assessment_demo_worker (--once): citation_job={citation_id} claim_job={claim_id}"
            )
            return _claim_exit_status(session_factory, claim_id)
        finally:
            engine.dispose()

    stop = False

    def _handle_signal(signum: int, _frame: object) -> None:
        nonlocal stop
        sig_name = signal.Signals(signum).name
        print(f"\nReceived {sig_name}; shutting down assessment demo worker gracefully...")
        stop = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    claim_description = (
        "pinned Transformers NLI; CPU, lazy load"
        if args.verifier == "real"
        else "deterministic stub; no AI model loaded"
    )
    print(
        f"EvalOps Assessment Demo Worker started.\n"
        f"  Database : {db_path} (isolated; {LIVE_DB_PATH.name} is untouched)\n"
        f"  Citation : real frozen citation-evaluator-v4\n"
        f"  Claim    : {args.verifier} ({claim_description})\n"
        f"Press Ctrl+C to stop."
    )

    try:
        while not stop:
            did_work = False
            citation_id = citation_worker.run_once()
            if citation_id is not None:
                print(f"[citation-worker] processed assessment #{citation_id}")
                did_work = True
            claim_id = claim_worker.run_once()
            if claim_id is not None:
                print(f"[claim-worker] processed assessment #{claim_id} ({args.verifier} mode)")
                if _claim_exit_status(session_factory, claim_id):
                    return 1
                did_work = True
            if not did_work:
                time.sleep(max(0.05, args.poll_seconds))
    finally:
        engine.dispose()
        print("Assessment demo worker stopped cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
