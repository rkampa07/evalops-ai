"""Canonical request and scoring-input fingerprints for durable evaluation jobs."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..schemas import RunCreate


def canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def canonical_request_hash(data: RunCreate) -> str:
    """Hash the validated request, treating response-array order as insignificant."""
    payload = data.model_dump(mode="json")
    payload["responses"] = sorted(payload["responses"], key=lambda response: response["case_key"])
    return canonical_json_hash(payload)


def scoring_input_hash(
    *,
    dataset_id: int,
    dataset_version: int,
    methodology_version: str,
    config: dict[str, Any],
    cases: list[dict[str, Any]],
    knowledge_base_hash: str | None,
    responses: list[dict[str, Any]],
) -> str:
    """Hash every persisted input that can affect deterministic scoring."""
    value = {
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "methodology_version": methodology_version,
        "config": config,
        "cases": sorted(cases, key=lambda case: (case["key"], case["id"])),
        "knowledge_base_hash": knowledge_base_hash,
        "responses": sorted(responses, key=lambda response: response["case_key"]),
    }
    return canonical_json_hash(value)
