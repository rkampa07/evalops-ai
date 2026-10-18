"""Model-free coverage for pinned setup and demo mode selection."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from evalops import nli_runtime
from evalops.claim_assessments import MODEL_ID, MODEL_REVISION
from evalops.semantic.nli import SemanticBackendUnavailable

SCRIPT = Path(__file__).resolve().parents[2] / "backend/scripts/assessment_demo_worker.py"
spec = importlib.util.spec_from_file_location("assessment_demo_worker", SCRIPT)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def test_cpu_factory_uses_production_verifier_without_a_simulated_fallback(monkeypatch):
    torch = SimpleNamespace(set_num_threads=Mock(), set_default_device=Mock())
    verifier = object()
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setattr(nli_runtime, "check_local_nli", Mock())
    monkeypatch.setattr(nli_runtime, "make_local_verifier", Mock(return_value=verifier))
    assert nli_runtime.make_cpu_verifier() is verifier
    torch.set_num_threads.assert_called_once_with(2)
    torch.set_default_device.assert_called_once_with("cpu")
    nli_runtime.make_local_verifier.assert_called_once_with()


def test_missing_local_dependencies_fail_without_loading_or_downloading(monkeypatch):
    monkeypatch.setattr(nli_runtime.importlib.util, "find_spec", lambda name: None)
    snapshot = Mock()
    monkeypatch.setattr(nli_runtime, "verify_cached_snapshot", snapshot)
    with pytest.raises(SemanticBackendUnavailable, match="make setup-nli"):
        nli_runtime.check_local_nli()
    snapshot.assert_not_called()


def test_explicit_download_is_pinned_and_limited_and_reuses_complete_cache(monkeypatch):
    record = {"commit": MODEL_REVISION}
    verify = Mock(side_effect=[SemanticBackendUnavailable("missing"), record, record])
    download = Mock()
    monkeypatch.setattr(nli_runtime, "verify_cached_snapshot", verify)
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    assert nli_runtime.download_pinned_nli() == record
    download.assert_called_once_with(
        repo_id=MODEL_ID,
        revision=MODEL_REVISION,
        allow_patterns=list(nli_runtime.PINNED_FILES),
        max_workers=1,
        token=False,
    )
    assert nli_runtime.download_pinned_nli() == record
    assert download.call_count == 1
    assert "pytorch_model.bin" not in nli_runtime.PINNED_FILES
    assert not any("onnx" in name for name in nli_runtime.PINNED_FILES)


@pytest.mark.parametrize("mode", [None, "real", "simulated"])
def test_demo_default_is_real_and_explicit_simulated_mode_still_works(tmp_path, monkeypatch, mode):
    captured = []
    preflight = Mock()
    monkeypatch.setattr(demo, "check_local_nli", preflight)
    monkeypatch.setattr(
        demo, "CitationAssessmentWorker", lambda factory: SimpleNamespace(run_once=lambda: None)
    )

    def worker(factory, *, verifier_factory):
        captured.append(verifier_factory)
        return SimpleNamespace(run_once=lambda: None)

    monkeypatch.setattr(demo, "ClaimAssessmentWorker", worker)
    args = ["--database-url", f"sqlite:///{tmp_path / 'demo.db'}", "--once"]
    if mode:
        args += ["--verifier", mode]
    assert demo.main(args) == 0
    assert captured == [
        demo.SimulatedDemoClaimVerifier if mode == "simulated" else demo.make_cpu_verifier
    ]
    assert preflight.call_count == (0 if mode == "simulated" else 1)


def test_real_preflight_failure_stops_before_touching_database(tmp_path, monkeypatch):
    monkeypatch.setattr(
        demo, "check_local_nli", Mock(side_effect=SemanticBackendUnavailable("missing snapshot"))
    )
    path = tmp_path / "must-not-exist.db"
    assert demo.main(["--database-url", f"sqlite:///{path}", "--once"]) == 2
    assert not path.exists()


def test_demo_rejects_live_database_in_both_modes(monkeypatch):
    preflight = Mock()
    monkeypatch.setattr(demo, "check_local_nli", preflight)
    for mode in ("real", "simulated"):
        assert (
            demo.main(["--database-url", f"sqlite:///{demo.LIVE_DB_PATH}", "--verifier", mode]) == 2
        )
    preflight.assert_not_called()
