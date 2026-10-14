"""Focused demo replay checks; no server, inference, or recording writes."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "backend/scripts/rag_demo.py"
spec = importlib.util.spec_from_file_location("rag_demo", SCRIPT)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


@pytest.mark.parametrize("version", ["1.3.0", "1.4.0", "2.0.0"])
def test_demo_reuses_only_current_methodology(monkeypatch, version):
    recording = json.loads(demo.BASELINE.read_text())
    existing = {
        "id": 7,
        "model_name": recording["model_name"],
        "prompt_version": recording["prompt_version"],
        "generation": recording["generation"],
        "methodology_version": version,
    }
    created = {
        **existing,
        "id": 8,
        "methodology_version": "2.0.0",
        "summary": {
            "passed_cases": 9,
            "total_cases": 20,
            "groundedness": 0.777778,
            "abstention_accuracy": 0.9,
        },
    }
    submissions = []

    def call(base, method, endpoint, body=None):
        if method == "GET":
            return {"items": [existing]}
        assert method == "POST" and endpoint == "/evaluations/runs"
        assert body["responses"] == recording["responses"]
        submissions.append(body)
        return created

    monkeypatch.setattr(demo, "call", call)
    result = demo.ensure_run("http://unused", 1, demo.BASELINE)
    assert result["id"] == (7 if version == "2.0.0" else 8)
    assert len(submissions) == (0 if version == "2.0.0" else 1)
    assert existing["methodology_version"] == version


# ------------------------------------------------------- live outputs never overwrite history

HISTORICAL = (
    "responses", "grades", "calibration", "benchmarks", "prospective", "experiments", "labels"
)  # fmt: skip


def run_attach(monkeypatch, mode, recording):
    commands = []
    monkeypatch.setattr(demo.subprocess, "run", lambda cmd, **kw: commands.append(cmd))
    monkeypatch.setattr(
        demo,
        "call",
        lambda *a, **k: {
            "model_graded": {"estimated_groundedness": 0.5, "heuristic_judge_agreement": 0.5}
        },
    )
    demo.attach_grades("http://unused", {"id": 1}, recording, mode)
    return commands[0]


@pytest.mark.parametrize("recording", list(demo.GRADES))
def test_live_grading_writes_only_to_the_live_directory(monkeypatch, recording):
    cmd = run_attach(monkeypatch, "live", recording)
    saved = Path(cmd[cmd.index("--save") + 1]).resolve()
    assert saved.is_relative_to(demo.LIVE_DIR.resolve())
    assert saved not in {p.resolve() for p in demo.GRADES.values()}


@pytest.mark.parametrize("recording", list(demo.GRADES))
def test_replay_still_reads_the_committed_grades(monkeypatch, recording):
    cmd = run_attach(monkeypatch, "replay", recording)
    assert Path(cmd[cmd.index("--grades") + 1]) == demo.GRADES[recording]


@pytest.mark.parametrize("folder", HISTORICAL)
def test_guard_rejects_historical_evidence_paths(folder):
    with pytest.raises(ValueError, match="historical"):
        demo.live_output_path(demo.RAG / folder / "anything.json")
    assert demo.live_output_path(demo.LIVE_DIR / "grades/x.json").is_relative_to(demo.LIVE_DIR)


def test_make_live_targets_never_write_historical_paths():
    import re
    import subprocess

    root = Path(__file__).resolve().parents[2]
    dry = subprocess.run(
        ["make", "-n", "rag-generate", "rag-calibrate"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout  # -n prints the commands without running any model
    targets = re.findall(r"(?:--out|--save)\s+(\S+)|2>\s*(\S+)", dry)
    paths = [a or b for a, b in targets]
    assert len(paths) >= 4
    for path in paths:
        assert path.startswith("examples/rag/live/"), path
