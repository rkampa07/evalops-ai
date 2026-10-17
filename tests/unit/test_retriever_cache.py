import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import pytest

import evalops.llm.retriever_cache as cache_module
from evalops.engine.rag import BM25Index
from evalops.llm.retriever_cache import RetrieverCache


def passages(size: int = 4, marker: str = "alpha") -> list[dict[str, str]]:
    return [
        {
            "passage_id": f"KB-{i:05d}",
            "title": f"{marker} policy topic {i}",
            "text": f"{marker} policy applies to customers about topic {i} with detail {i % 7}.",
        }
        for i in range(size)
    ]


def test_cache_hits_for_same_dataset_and_content():
    cache = RetrieverCache()
    kb = passages()
    first = cache.get(10, kb)
    second = cache.get(10, kb)

    assert first is second
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 1
    assert cache.stats()["hit_rate"] == 0.5


def test_dataset_identity_separates_equal_content():
    cache = RetrieverCache()
    kb = passages()
    assert cache.get(10, kb) is not cache.get(11, kb)
    assert cache.stats()["entries"] == 2


def test_kb_text_or_title_change_invalidates_and_rebuilds():
    cache = RetrieverCache()
    old = passages()
    before = cache.get(10, old)
    changed = [p.copy() for p in old]
    changed[0]["title"] = "unique zebra policy"
    after_title = cache.get(10, changed)
    assert after_title is not before
    assert cache.stats()["invalidations"] == 1
    assert cache.stats()["entries"] == 1

    changed[0]["text"] += " revised"
    after_text = cache.get(10, changed)
    assert after_text is not after_title
    assert cache.stats()["invalidations"] == 2
    assert cache.stats()["entries"] == 1


@pytest.mark.parametrize("query", ["alpha policy topic", "detail 3", "unmatched query"])
def test_cached_ranking_and_scores_match_existing_bm25(query):
    kb = passages(17)
    source = {p["passage_id"]: f"{p['title']}. {p['text']}" for p in kb}
    old_index = BM25Index(source)
    expected_scores = old_index.score(query)
    expected_ids = old_index.search(query, 5)

    retriever = RetrieverCache().get(1, kb, k=5)
    actual, actual_scores = retriever.retrieve_with_scores(query)

    assert [p["passage_id"] for p in actual] == expected_ids
    assert actual_scores == expected_scores


def test_retrieval_trace_fields_match_previous_score_and_search_path():
    kb = passages(8)
    query = "alpha policy detail 2"
    source = {p["passage_id"]: f"{p['title']}. {p['text']}" for p in kb}
    old_index = BM25Index(source)
    old_scores = old_index.score(query)
    old_ids = old_index.search(query, 3)
    old_by_id = {p["passage_id"]: p for p in kb}
    expected_trace_passages = [
        old_by_id[pid] | {"bm25_score": round(old_scores[pid], 4)} for pid in old_ids
    ]

    retrieved, scores = RetrieverCache().get(3, kb).retrieve_with_scores(query)
    actual_trace_passages = [
        p | {"bm25_score": round(scores[p["passage_id"]], 4)} for p in retrieved
    ]
    assert actual_trace_passages == expected_trace_passages


def test_concurrent_misses_build_one_shared_index(monkeypatch):
    real_retriever = cache_module.Retriever
    build_lock = Lock()
    builds = 0

    def counted_retriever(*args, **kwargs):
        nonlocal builds
        with build_lock:
            builds += 1
        time.sleep(0.02)
        return real_retriever(*args, **kwargs)

    monkeypatch.setattr(cache_module, "Retriever", counted_retriever)
    cache = RetrieverCache()
    kb = passages(40)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: cache.get(5, kb), range(8)))

    assert builds == 1
    assert all(result is results[0] for result in results)
    assert cache.stats()["hits"] == 7


def test_lru_eviction_keeps_entry_and_memory_proxies_within_bounds():
    cache = RetrieverCache(max_entries=2, max_source_bytes=10_000, max_indexed_terms=10_000)
    kb = passages(6)
    first = cache.get(1, kb)
    cache.get(2, kb)
    assert cache.get(1, kb) is first  # make dataset 2 the least-recently used entry
    cache.get(3, kb)

    stats = cache.stats()
    assert stats["entries"] == 2
    assert stats["evictions"] == 1
    assert stats["cached_source_bytes"] <= cache.max_source_bytes
    assert stats["cached_indexed_terms"] <= cache.max_indexed_terms


def test_oversized_index_is_returned_but_not_retained():
    cache = RetrieverCache(max_source_bytes=20, max_indexed_terms=100)
    kb = passages(2)
    first = cache.get(1, kb)
    second = cache.get(1, kb)
    assert first is not second
    assert cache.stats()["entries"] == 0
    assert cache.stats()["bypasses"] == 2
