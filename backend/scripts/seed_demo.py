"""Load the fictional Kestrel Home Goods demo through the public HTTP API.

Usage (with the API running):
    backend/.venv/bin/python backend/scripts/seed_demo.py [--base-url http://127.0.0.1:8000]

Idempotent: if the dataset already exists, it is reused and its runs are not duplicated.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
RUN_FILES = ["baseline_prod_v1.3", "candidate_v1.4_regressed", "candidate_v1.5_fixed"]


def call(base: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        sys.exit(f"{method} {path} failed with HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"cannot reach API at {base}: {exc.reason}. Is the backend running?")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    fixture = json.loads((EXAMPLES / "datasets" / "kestrel_support_v1.json").read_text())
    existing = {d["name"]: d for d in call(base, "GET", "/datasets")}
    if fixture["name"] in existing:
        ds = existing[fixture["name"]]
        print(f"dataset '{ds['name']}' already exists (id={ds['id']})")
    else:
        ds = call(
            base,
            "POST",
            "/datasets",
            {"name": fixture["name"], "description": fixture["description"]},
        )
        call(
            base,
            "POST",
            f"/datasets/{ds['id']}/test-cases/bulk",
            {"test_cases": fixture["test_cases"]},
        )
        print(
            f"created dataset '{ds['name']}' (id={ds['id']}) with "
            f"{len(fixture['test_cases'])} test cases"
        )

    runs = call(base, "GET", f"/evaluations/runs?dataset_id={ds['id']}&limit=200")["items"]
    seen = {(r["model_name"], r["prompt_version"]) for r in runs}
    ids = {}
    for name in RUN_FILES:
        body = json.loads((EXAMPLES / "responses" / f"{name}.json").read_text())
        key = (body["model_name"], body["prompt_version"])
        if key in seen:
            ids[name] = next(r["id"] for r in runs if (r["model_name"], r["prompt_version"]) == key)
            print(f"run {name} already exists (id={ids[name]})")
            continue
        run = call(base, "POST", "/evaluations/runs", body | {"dataset_id": ds["id"]})
        ids[name] = run["id"]
        s = run["summary"]
        print(f"run {name}: id={run['id']} passed {s['passed_cases']}/{s['total_cases']}")

    b, c = ids["baseline_prod_v1.3"], ids["candidate_v1.4_regressed"]
    print(f"\nTry: {base}/evaluations/compare?baseline_run_id={b}&candidate_run_id={c}")


if __name__ == "__main__":
    main()
