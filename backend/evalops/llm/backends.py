"""Model backends: MLX (Apple Silicon, optional) and a deterministic fixture backend."""

from __future__ import annotations

import hashlib
import json
import platform
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Protocol

Messages = list[dict[str, str]]


@dataclass(frozen=True)
class GenerationSettings:
    max_tokens: int = 256
    temperature: float = 0.0  # greedy decoding: reproducible for a fixed model + prompt
    seed: int = 0


@dataclass
class Generation:
    text: str
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tokens_per_second: float | None = None
    finish_reason: str | None = None
    peak_memory_mb: float | None = None
    extra: dict = field(default_factory=dict)

    def metadata(self) -> dict:
        data = asdict(self)
        data.pop("text")
        data.pop("extra")
        return {k: v for k, v in data.items() if v is not None} | self.extra


class Backend(Protocol):
    name: str
    model_id: str
    model_revision: str | None

    def generate(self, messages: Messages, settings: GenerationSettings) -> Generation: ...

    def describe(self) -> dict: ...


def messages_key(messages: Messages) -> str:
    return hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()


class FixtureBackend:
    """Replays recorded outputs (keyed by the exact prompt messages) or a function.

    Used in tests and to replay the committed demo without any model installed.
    """

    name = "fixture"

    def __init__(
        self,
        model_id: str,
        outputs: dict[str, str] | Callable[[Messages], str],
        model_revision: str | None = None,
    ):
        self.model_id = model_id
        self.model_revision = model_revision
        self._outputs = outputs

    def generate(self, messages: Messages, settings: GenerationSettings) -> Generation:
        start = time.perf_counter()
        if callable(self._outputs):
            text = self._outputs(messages)
        else:
            key = messages_key(messages)
            if key not in self._outputs:
                raise KeyError(f"no recorded output for prompt {key[:12]}")
            text = self._outputs[key]
        return Generation(text=text, latency_ms=(time.perf_counter() - start) * 1000)

    def describe(self) -> dict:
        return {
            "backend": self.name,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
        }


class MLXBackend:
    """Local generation with mlx-lm on Apple Silicon. Loads weights on first use."""

    name = "mlx"

    def __init__(self, model_id: str):
        self.model_id = model_id
        self.model_revision: str | None = None
        self.load_seconds: float | None = None
        self._model = None
        self._tokenizer = None

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            from huggingface_hub import snapshot_download
            from mlx_lm import load
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "MLX backend needs the optional dependencies: pip install -e '.[local-ai]'"
            ) from exc
        # local_files_only: never download implicitly; downloads are an explicit, approved step.
        path = Path(snapshot_download(self.model_id, local_files_only=True))
        self.model_revision = path.name  # snapshot directory = Hugging Face commit sha
        start = time.perf_counter()
        self._model, self._tokenizer = load(str(path))
        self.load_seconds = time.perf_counter() - start

    def generate(self, messages: Messages, settings: GenerationSettings) -> Generation:
        import mlx.core as mx
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler

        self.load()
        mx.random.seed(settings.seed)
        prompt = self._tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False
        )
        sampler = make_sampler(temp=settings.temperature)
        start = time.perf_counter()
        text, last = "", None
        for chunk in stream_generate(
            self._model, self._tokenizer, prompt, max_tokens=settings.max_tokens, sampler=sampler
        ):
            text += chunk.text
            last = chunk
        latency_ms = (time.perf_counter() - start) * 1000
        return Generation(
            text=text.strip(),
            latency_ms=round(latency_ms, 1),
            prompt_tokens=getattr(last, "prompt_tokens", None),
            completion_tokens=getattr(last, "generation_tokens", None),
            tokens_per_second=round(last.generation_tps, 2) if last else None,
            finish_reason=getattr(last, "finish_reason", None),
            peak_memory_mb=round(last.peak_memory * 1024, 1) if last else None,
        )

    def describe(self) -> dict:
        def version(pkg: str) -> str | None:
            try:
                return metadata.version(pkg)
            except metadata.PackageNotFoundError:  # pragma: no cover
                return None

        return {
            "backend": self.name,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "mlx": version("mlx"),
            "mlx_lm": version("mlx-lm"),
            "machine": platform.machine(),
            "platform": platform.platform(terse=True),
        }
