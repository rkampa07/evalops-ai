"""Committed RAG evidence is read-only for scripts; live outputs go to examples/rag/live/."""

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backend/scripts"
RAG = ROOT / "examples/rag"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evidence = load("_evidence")
generate = load("rag_generate")
judge = load("rag_judge")

COMMITTED_GRADES = RAG / "grades/llama3.2-3b_judge-v2_on_qwen-rag-v1.json"
COMMITTED_RESPONSES = RAG / "responses/qwen2.5-1.5b_rag-v1.json"
COMMITTED_CALIBRATION = RAG / "calibration/llama3.2-3b_judge-v2.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "path",
    [
        *(RAG / d / "new.json" for d in evidence.HISTORICAL_DIR_NAMES),
        COMMITTED_RESPONSES,
        RAG / "kestrel_policy_kb.json",
        RAG / "live/../grades/escape.json",  # traversal out of live/ is resolved first
    ],
)
def test_rejects_historical_evidence(path):
    with pytest.raises(ValueError, match="historical evidence"):
        evidence.assert_writable(path)


@pytest.mark.parametrize("path", [RAG / "live/responses/x.json", Path("/tmp/evalops-scratch.json")])
def test_allows_live_and_scratch_outputs(path):
    assert evidence.assert_writable(path) == path


def test_live_output_path_requires_live_directory(tmp_path):
    assert evidence.live_output_path(RAG / "live/grades/x.json").is_relative_to(evidence.LIVE_DIR)
    with pytest.raises(ValueError, match="live outputs must be under"):
        evidence.live_output_path(tmp_path / "x.json")


class NoModel:
    def __init__(self, *a, **k):
        raise AssertionError("a model must not be loaded when the output path is rejected")


def test_generate_refuses_committed_responses_before_loading_a_model(monkeypatch):
    before = digest(COMMITTED_RESPONSES)
    monkeypatch.setattr(generate, "MLXBackend", NoModel)
    argv = ["rag_generate.py", "--prompt-version", "rag-v1", "--model", "m",
            "--out", str(COMMITTED_RESPONSES)]  # fmt: skip
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        generate.main()
    assert exc.value.code == 2 and digest(COMMITTED_RESPONSES) == before


@pytest.mark.parametrize(
    "argv",
    [
        ["calibrate", "--out", str(COMMITTED_CALIBRATION)],
        ["grade-run", "--run-id", "1", "--save", str(COMMITTED_GRADES)],
    ],
)
def test_judge_refuses_committed_outputs_before_any_work(monkeypatch, argv):
    targets = [COMMITTED_CALIBRATION, COMMITTED_GRADES]
    before = [digest(p) for p in targets]
    monkeypatch.setattr(judge, "MLXBackend", NoModel)
    monkeypatch.setattr(judge, "call", lambda *a, **k: pytest.fail("no API call expected"))
    monkeypatch.setattr(sys, "argv", ["rag_judge.py", *argv])
    with pytest.raises(SystemExit) as exc:
        judge.main()
    assert exc.value.code == 2 and [digest(p) for p in targets] == before


def test_judge_still_reads_committed_grades(monkeypatch):
    before = digest(COMMITTED_GRADES)
    sent = []
    monkeypatch.setattr(
        judge,
        "call",
        lambda base, method, path, body=None: sent.append(body) or {"model_graded": {}},
    )
    argv = ["rag_judge.py", "attach", "--run-id", "1", "--grades", str(COMMITTED_GRADES)]
    monkeypatch.setattr(sys, "argv", argv)
    judge.main()
    assert sent and sent[0]["grades"] and digest(COMMITTED_GRADES) == before


def test_heuristic_calibration_to_scratch_is_allowed(monkeypatch, tmp_path):
    out = tmp_path / "heuristic.json"
    monkeypatch.setattr(judge, "MLXBackend", NoModel)
    monkeypatch.setattr(
        sys, "argv", ["rag_judge.py", "calibrate", "--heuristic-only", "--out", str(out)]
    )
    judge.main()
    assert out.exists()
