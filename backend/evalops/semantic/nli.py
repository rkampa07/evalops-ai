"""NLI backends. Each returns {entailment, neutral, contradiction} probabilities per pair.

TransformersNLI loads lazily and only from the local Hugging Face cache. The requested revision
must be a full commit SHA. Before loading, verify_cached_snapshot() checks that the config,
weights and tokenizer files all resolve inside cache/<repo>/snapshots/<that SHA>/, and the model
is then loaded from that directory, so the files verified are exactly the files loaded (this
doesn't rely on model.config._commit_hash, which transformers 5.x no longer sets). It needs
`torch`, which is NOT a default dependency: installing it and downloading a model both need the
project owner's approval.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from .verdict import CONTRADICTION, ENTAILMENT, NEUTRAL

Pair = tuple[str, str]  # (premise, hypothesis)


class SemanticBackendUnavailable(RuntimeError):
    pass


COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def require_commit_sha(revision: str | None) -> str:
    """Only a full 40-hex commit SHA is immutable. Branches ("main"), tags ("v1.0") and short
    SHAs can move or be ambiguous, so they are rejected."""
    if not revision or not COMMIT_SHA_RE.match(revision):
        raise ValueError(
            f"revision {revision!r} is not an immutable commit: pass the full 40-character "
            "lowercase commit SHA (not a branch, tag or short SHA)"
        )
    return revision


WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")
TOKENIZER_VOCAB_FILES = (
    "tokenizer.json",
    "spm.model",
    "sentencepiece.bpe.model",
    "vocab.txt",
    "vocab.json",
)
_SMALL_FILE = 5 * 1024 * 1024


def verify_cached_snapshot(
    model_id: str, revision: str, cache_dir: str | Path | None = None
) -> dict:
    """Prove that the model and tokenizer files for `revision` are in the local cache under
    snapshots/<revision>/. Fails closed (SemanticBackendUnavailable) otherwise. No network."""
    require_commit_sha(revision)
    from huggingface_hub import try_to_load_from_cache

    def find(name: str) -> Path | None:
        path = try_to_load_from_cache(model_id, name, cache_dir=cache_dir, revision=revision)
        return Path(path) if isinstance(path, str) else None

    config = find("config.json")
    if config is None:
        raise SemanticBackendUnavailable(
            f"{model_id}@{revision} is not in the local cache (no config.json in that snapshot); "
            "downloading it needs approval"
        )
    snapshot = config.parent
    if snapshot.name != revision or snapshot.parent.name != "snapshots":
        raise SemanticBackendUnavailable(
            f"config.json resolved outside snapshots/{revision}: {config}"
        )
    files = {"config.json": config}
    weights = next(((n, p) for n in WEIGHT_FILES if (p := find(n))), None)
    if weights is None:
        raise SemanticBackendUnavailable(f"no model weights in snapshot {revision}")
    files[weights[0]] = weights[1]
    tokenizer_config = find("tokenizer_config.json")
    vocab = next(((n, p) for n in TOKENIZER_VOCAB_FILES if (p := find(n))), None)
    if tokenizer_config is None or vocab is None:
        raise SemanticBackendUnavailable(f"tokenizer files missing from snapshot {revision}")
    files["tokenizer_config.json"] = tokenizer_config
    files[vocab[0]] = vocab[1]

    record = {}
    for name, path in files.items():
        if path.parent != snapshot or not path.exists():
            raise SemanticBackendUnavailable(f"{name} doesn't resolve inside snapshot {revision}")
        real = Path(os.path.realpath(path))
        entry = {"blob": real.name, "bytes": real.stat().st_size}
        if entry["bytes"] <= _SMALL_FILE:
            entry["sha256"] = hashlib.sha256(real.read_bytes()).hexdigest()
        record[name] = entry
    return {"commit": revision, "snapshot_dir": str(snapshot), "files": record}


class FixtureNLI:
    """Deterministic stand-in for tests: a function from (premise, hypothesis) to scores."""

    def __init__(self, fn: Callable[[str, str], dict[str, float]], model_id: str = "fixture/nli"):
        self.fn = fn
        self.model_id = model_id

    def score(self, pairs: Sequence[Pair]) -> list[dict[str, float]]:
        return [self.fn(p, h) for p, h in pairs]

    def load(self) -> float:
        return 0.0

    def describe(self) -> dict:
        return {"backend": "fixture", "model_id": self.model_id, "revision": None}


class TransformersNLI:
    """A local Hugging Face 3-way NLI classifier (for example a DeBERTa-v3 cross-encoder)."""

    def __init__(
        self,
        model_id: str,
        revision: str,
        max_length: int = 512,
        batch_size: int = 16,
        cache_dir: str | Path | None = None,
    ):
        self.model_id, self.revision = model_id, require_commit_sha(revision)
        self.max_length, self.batch_size, self.cache_dir = max_length, batch_size, cache_dir
        self._model = self._tokenizer = self._labels = None
        self.load_seconds: float | None = None
        self.resolved: dict = {}

    def load(self) -> float:
        """Verify the cached snapshot, then load from it (local files only). Returns seconds."""
        if self._model is not None:
            return 0.0
        start = time.perf_counter()
        verified = verify_cached_snapshot(self.model_id, self.revision, self.cache_dir)
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:
            raise SemanticBackendUnavailable(
                "torch is not installed. The semantic evaluator's NLI backend is optional, and "
                "installing it needs approval."
            ) from exc
        torch.manual_seed(0)
        snapshot = verified["snapshot_dir"]
        try:
            tokenizer = AutoTokenizer.from_pretrained(snapshot, local_files_only=True)
            model = AutoModelForSequenceClassification.from_pretrained(
                snapshot, local_files_only=True
            )
        except OSError as exc:
            raise SemanticBackendUnavailable(f"loading from {snapshot} failed: {exc}") from exc
        self.resolved = verified
        model.eval()
        labels = {}
        for idx, name in model.config.id2label.items():
            low = name.lower()
            key = (
                ENTAILMENT
                if "entail" in low
                else CONTRADICTION
                if "contra" in low
                else NEUTRAL
                if "neutral" in low
                else None
            )
            if key is None:
                raise SemanticBackendUnavailable(
                    f"unexpected label {name!r}; not a 3-way NLI model"
                )
            labels[int(idx)] = key
        self._model, self._tokenizer, self._labels = model, tokenizer, labels
        self.load_seconds = round(time.perf_counter() - start, 3)
        return self.load_seconds

    def score(self, pairs: Sequence[Pair]) -> list[dict[str, float]]:
        self.load()
        import torch

        out = []
        for i in range(0, len(pairs), self.batch_size):
            batch = pairs[i : i + self.batch_size]
            enc = self._tokenizer(
                [p for p, _ in batch],
                [h for _, h in batch],
                truncation=True,
                max_length=self.max_length,
                padding=True,
                return_tensors="pt",
            )
            with torch.no_grad():
                probs = torch.softmax(self._model(**enc).logits, dim=-1).tolist()
            out += [{self._labels[j]: round(p, 6) for j, p in enumerate(row)} for row in probs]
        return out

    def describe(self) -> dict:
        info = {
            "backend": "transformers",
            "model_id": self.model_id,
            "revision": self.revision,
            "resolved_snapshot_commit": self.resolved.get("commit"),
            "snapshot_files": self.resolved.get("files"),
            "max_length": self.max_length,
        }
        try:
            import torch
            import transformers

            info |= {"torch": torch.__version__, "transformers": transformers.__version__}
        except ImportError:
            pass
        return info
