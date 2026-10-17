"""Bounded process-local cache for immutable chat BM25 indexes."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .rag_pipeline import Retriever


def _field(passage: Any, name: str, default: str = "") -> str:
    if isinstance(passage, Mapping):
        value = passage.get(name, default)
    else:
        value = getattr(passage, name, default)
    return str(value)


def retriever_content_fingerprint(passages: Sequence[Any]) -> tuple[str, int]:
    """Hash every BM25 input field (ID, title, text) and return its UTF-8 source-byte size.

    The existing run-level KB hash intentionally omits titles. BM25 indexes titles, so using
    that hash alone could incorrectly reuse an index after a title edit. Length-prefix each
    canonical JSON record to make the streaming digest unambiguous without building another
    full-KB JSON string.
    """
    records = sorted(
        (_field(p, "passage_id"), _field(p, "title"), _field(p, "text")) for p in passages
    )
    digest = hashlib.sha256()
    source_bytes = 0
    for record in records:
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        source_bytes += len(encoded)
    return digest.hexdigest(), source_bytes


@dataclass(frozen=True)
class _Entry:
    retriever: Retriever
    source_bytes: int
    indexed_terms: int


class RetrieverCache:
    """Thread-safe LRU bounded by entry count, indexed terms, and source text bytes.

    Source bytes and term count are deterministic size proxies rather than exact Python RSS.
    An individual KB exceeding either limit is indexed for the request but is not retained.
    Old hashes for the same dataset identity are evicted as soon as a new content hash arrives.
    """

    def __init__(
        self,
        *,
        max_entries: int = 4,
        max_source_bytes: int = 8 * 1024 * 1024,
        max_indexed_terms: int = 300_000,
    ) -> None:
        if max_entries < 1 or max_source_bytes < 1 or max_indexed_terms < 1:
            raise ValueError("retriever cache bounds must all be positive")
        self.max_entries = max_entries
        self.max_source_bytes = max_source_bytes
        self.max_indexed_terms = max_indexed_terms
        self._entries: OrderedDict[tuple[int, str, int], _Entry] = OrderedDict()
        self._source_bytes = 0
        self._indexed_terms = 0
        self._lock = threading.RLock()
        self._hits = self._misses = self._evictions = self._invalidations = 0
        self._bypasses = 0

    def get(self, dataset_id: int, passages: Sequence[Any], k: int = 3) -> Retriever:
        content_hash, source_bytes = retriever_content_fingerprint(passages)
        key = (dataset_id, content_hash, k)
        with self._lock:
            # The KB is the cache validity source. Dataset.version is intentionally not used:
            # some passage metadata edits historically did not increment it, while unrelated
            # case edits should not force an index rebuild.
            for stale_key in [
                old for old in self._entries if old[0] == dataset_id and old[1] != content_hash
            ]:
                self._drop(stale_key)
                self._invalidations += 1

            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                self._hits += 1
                return cached.retriever

            self._misses += 1
            retriever = Retriever(passages, k=k)
            indexed_terms = retriever.index.term_count
            if source_bytes > self.max_source_bytes or indexed_terms > self.max_indexed_terms:
                self._bypasses += 1
                return retriever

            while self._entries and (
                len(self._entries) >= self.max_entries
                or self._source_bytes + source_bytes > self.max_source_bytes
                or self._indexed_terms + indexed_terms > self.max_indexed_terms
            ):
                oldest = next(iter(self._entries))
                self._drop(oldest)
                self._evictions += 1

            self._entries[key] = _Entry(retriever, source_bytes, indexed_terms)
            self._source_bytes += source_bytes
            self._indexed_terms += indexed_terms
            return retriever

    def _drop(self, key: tuple[int, str, int]) -> None:
        entry = self._entries.pop(key)
        self._source_bytes -= entry.source_bytes
        self._indexed_terms -= entry.indexed_terms

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._source_bytes = self._indexed_terms = 0

    def stats(self) -> dict[str, int | float]:
        with self._lock:
            attempts = self._hits + self._misses
            return {
                "entries": len(self._entries),
                "cached_source_bytes": self._source_bytes,
                "cached_indexed_terms": self._indexed_terms,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "invalidations": self._invalidations,
                "bypasses": self._bypasses,
                "hit_rate": self._hits / attempts if attempts else 0.0,
            }
