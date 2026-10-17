"""Chat inference runtime: backend selection, lazy model, single-generation limit."""

from __future__ import annotations

import re
import threading

from ..errors import BusyError, ChatDisabledError
from ..llm.backends import FixtureBackend, Generation, GenerationSettings, Messages, MLXBackend

_PASSAGE_LINE_RE = re.compile(r"^\[([A-Z][A-Z0-9-]+)\] [^:\n]*: (.+)$", re.MULTILINE)


def fixture_reply(messages: Messages) -> str:
    """Deterministic stand-in for a model (tests, CI, UI demos without MLX).

    Quotes the first sentence of the top retrieved passage and cites it. Not a real model.
    """
    found = _PASSAGE_LINE_RE.findall(messages[-1]["content"])
    if not found:
        return "I don't have that information in our policies."
    passage_id, text = found[0]
    first_sentence = re.split(r"(?<=[.!?])\s", text.strip())[0]
    return f"{first_sentence} [{passage_id}]"


FIXTURE_MODEL_ID = "fixture/echo-top-passage"


class ChatRuntime:
    """Holds at most one model and allows one generation at a time (no queueing)."""

    def __init__(self, mode: str, model_id: str, backend=None):
        self.mode = mode
        self.model_id = model_id
        self._backend = backend
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def _get_backend(self):
        if self._backend is None:
            if self.mode == "fixture":
                self._backend = FixtureBackend(FIXTURE_MODEL_ID, fixture_reply)
            elif self.mode == "mlx":
                self._backend = MLXBackend(self.model_id)  # weights load on first generate
        return self._backend

    def generate(self, messages: Messages, settings: GenerationSettings) -> tuple[Generation, dict]:
        if not self.enabled:
            raise ChatDisabledError(
                "live chat is disabled; start the API with EVALOPS_CHAT_BACKEND=fixture or mlx"
            )
        if not self._lock.acquire(blocking=False):
            raise BusyError("the model is already generating a reply; try again in a moment")
        try:
            backend = self._get_backend()
            generation = backend.generate(messages, settings)
            return generation, backend.describe()
        finally:
            self._lock.release()

    def status(self) -> dict:
        backend = self._backend
        loaded = bool(backend) and (
            self.mode != "mlx" or getattr(backend, "_model", None) is not None
        )
        if backend is not None:
            model_id = backend.model_id
        else:
            model_id = {"off": None, "fixture": FIXTURE_MODEL_ID}.get(self.mode, self.model_id)
        return {
            "enabled": self.enabled,
            "backend": self.mode,
            "model_id": model_id,
            "model_revision": getattr(backend, "model_revision", None),
            "loaded": loaded,
            "busy": self._lock.locked(),
        }
