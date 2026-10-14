"""Write protection for committed RAG evidence, shared by the RAG scripts.

Everything under examples/rag/ is committed evidence (recordings, grades, calibration, benchmarks,
prospective sets, experiments, labels, datasets) and is read-only for these scripts. Scratch and
live outputs belong in examples/rag/live/ (git-ignored) or outside examples/rag/.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAG = ROOT / "examples/rag"
LIVE_DIR = RAG / "live"
HISTORICAL_DIR_NAMES = (
    "responses", "grades", "calibration", "benchmarks", "prospective", "experiments", "labels"
)  # fmt: skip


def assert_writable(path: Path) -> Path:
    """Return `path` unless it would write inside committed evidence (examples/rag/, not live/)."""
    resolved = Path(path).resolve()  # resolves ".." and symlinks before checking
    if resolved.is_relative_to(RAG.resolve()) and not resolved.is_relative_to(LIVE_DIR.resolve()):
        raise ValueError(
            f"refusing to write into historical evidence: {path}. "
            f"Write live or scratch outputs under {LIVE_DIR.relative_to(ROOT)}/ instead."
        )
    return path


def live_output_path(path: Path) -> Path:
    """Like assert_writable, but additionally require the live directory (used by the demo)."""
    assert_writable(path)
    if not Path(path).resolve().is_relative_to(LIVE_DIR.resolve()):
        raise ValueError(f"live outputs must be under {LIVE_DIR}: {path}")
    return path
