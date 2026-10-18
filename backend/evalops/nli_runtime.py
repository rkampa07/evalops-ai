"""Optional CPU runtime/setup for the existing frozen NLI evaluator; no API model loading."""

from __future__ import annotations

import importlib.util

from .claim_assessments import MODEL_ID, MODEL_REVISION, make_local_verifier
from .semantic.nli import SemanticBackendUnavailable, verify_cached_snapshot

# One safetensors checkpoint, configuration, and tokenizer assets; no ONNX or pickle weights.
PINNED_FILES = (
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "added_tokens.json",
    "spm.model",
)


def check_local_nli() -> dict:
    """Preflight dependencies and the exact cache snapshot without importing ML frameworks."""
    missing = [
        name
        for name in ("torch", "transformers", "huggingface_hub", "sentencepiece")
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        raise SemanticBackendUnavailable(
            f"Missing optional NLI packages: {', '.join(missing)}. Run make setup-nli."
        )
    return verify_cached_snapshot(MODEL_ID, MODEL_REVISION)


def download_pinned_nli() -> dict:
    """Explicit setup only. Reuse a complete cache; otherwise download only this pinned model."""
    try:
        return verify_cached_snapshot(MODEL_ID, MODEL_REVISION)
    except SemanticBackendUnavailable:
        pass
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=MODEL_ID,
        revision=MODEL_REVISION,
        allow_patterns=list(PINNED_FILES),
        max_workers=1,
        token=False,
    )
    return verify_cached_snapshot(MODEL_ID, MODEL_REVISION)


def make_cpu_verifier():
    """Use the production verifier unchanged, with CPU tensors and two intra-op threads."""
    check_local_nli()
    import torch

    torch.set_num_threads(2)
    torch.set_default_device("cpu")
    return make_local_verifier()
