"""Generate RAG answers for the Kestrel policy dataset with a local model.

Retrieval (BM25, top-k) -> prompt version -> local model -> response file that can be posted to
POST /evaluations/runs (plus a `benchmark` section). One model is loaded at a time.

    backend/.venv/bin/python backend/scripts/rag_generate.py --prompt-version rag-v1 \\
        --model mlx-community/Qwen2.5-1.5B-Instruct-4bit \\
        --out examples/rag/responses/qwen1.5b_rag-v1.json

Requires the optional `local-ai` extra and a model already downloaded to the Hugging Face cache
(the script never downloads weights itself).
"""

from __future__ import annotations

import argparse
import json
import platform
import resource
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _evidence import assert_writable  # noqa: E402

from evalops.engine.versioning import METHODOLOGY_VERSION  # noqa: E402
from evalops.llm.backends import GenerationSettings, MLXBackend  # noqa: E402
from evalops.llm.rag_pipeline import PROMPTS, Retriever, build_messages, prompt_hash  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KB = ROOT / "examples/rag/kestrel_policy_kb.json"


def max_rss_mb() -> float:
    # ru_maxrss is bytes on macOS, kilobytes on Linux
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(rss / (1024 * 1024) if platform.system() == "Darwin" else rss / 1024, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prompt-version", required=True, choices=sorted(PROMPTS))
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--kb", type=Path, default=DEFAULT_KB)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--limit", type=int, default=None, help="only the first N questions")
    args = parser.parse_args()
    try:  # fail before loading any model
        assert_writable(args.out)
    except ValueError as exc:
        parser.error(str(exc))

    dataset = json.loads(args.kb.read_text())
    retriever = Retriever(dataset["passages"], k=args.k)
    settings = GenerationSettings(max_tokens=args.max_tokens)
    backend = MLXBackend(args.model)
    rss_before_load = max_rss_mb()
    backend.load()
    cases = dataset["test_cases"][: args.limit]
    # One untimed warm-up generation: the first call compiles Metal kernels and would
    # otherwise inflate the first question's latency. Its duration is reported separately.
    warmup = backend.generate(
        build_messages(args.prompt_version, "warm-up", retriever.retrieve("returns")), settings
    )

    responses = []
    started = time.perf_counter()
    for i, case in enumerate(cases, start=1):
        passages = retriever.retrieve(case["input"])
        out = backend.generate(
            build_messages(args.prompt_version, case["input"], passages), settings
        )
        responses.append(
            {
                "case_key": case["key"],
                "output": out.text,
                "latency_ms": out.latency_ms,
                "retrieved_passage_ids": [p["passage_id"] for p in passages],
                "generation": out.metadata(),
            }
        )
        print(
            f"[{i}/{len(cases)}] {case['key']}: {out.latency_ms:.0f} ms, "
            f"{out.completion_tokens} tok, {out.tokens_per_second} tok/s",
            flush=True,
        )

    latencies = sorted(r["latency_ms"] for r in responses)
    peak_metal = max(r["generation"].get("peak_memory_mb", 0) for r in responses)
    record = {
        "model_name": args.model,
        "prompt_version": args.prompt_version,
        "label": f"{args.model.split('/')[-1]} with prompt {args.prompt_version}",
        "config": {"min_token_f1": 0.5, "min_groundedness": 0.8},
        "generation": backend.describe()
        | {
            "prompt_version": args.prompt_version,
            "prompt_hash": prompt_hash(args.prompt_version),
            "settings": {
                "temperature": settings.temperature,
                "max_tokens": settings.max_tokens,
                "seed": settings.seed,
            },
            "retrieval": retriever.describe(),
            "knowledge_base": args.kb.name,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "evalops_methodology_at_generation": METHODOLOGY_VERSION,
        },
        "benchmark": {
            "questions": len(responses),
            "model_load_seconds": round(backend.load_seconds or 0, 2),
            "warmup_latency_ms": warmup.latency_ms,
            "total_generation_seconds": round(time.perf_counter() - started, 2),
            "latency_ms_p50": latencies[(len(latencies) - 1) // 2],
            "latency_ms_max": latencies[-1],
            "peak_metal_memory_mb": peak_metal,
            "process_max_rss_mb": max_rss_mb(),
            "process_rss_before_model_load_mb": rss_before_load,
        },
        "responses": responses,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record["benchmark"], indent=2))


if __name__ == "__main__":
    main()
