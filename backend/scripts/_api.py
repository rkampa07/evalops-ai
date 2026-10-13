"""Tiny JSON-over-HTTP client for the EvalOps API (stdlib only)."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


def call(base: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        sys.exit(
            f"{method} {path} failed with HTTP {exc.code}: {exc.read().decode(errors='replace')}"
        )
    except urllib.error.URLError as exc:
        sys.exit(f"cannot reach API at {base}: {exc.reason}. Is the backend running?")
