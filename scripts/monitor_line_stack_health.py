from __future__ import annotations

"""Record timestamped local stack availability during a controlled restart."""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "logs" / "line_model_shadow" / "line_restart_health.jsonl"
ENDPOINTS = {
    "market": "http://127.0.0.1:8010/healthz",
    "line": "http://127.0.0.1:8021/healthz",
    "ollama": "http://127.0.0.1:8020/api/version",
}


def _probe(name: str, url: str, timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        response = requests.get(url, timeout=timeout_seconds)
        payload = response.json() if response.content else {}
        ready = response.status_code == 200
        if name == "line":
            ready = ready and payload.get("status") == "ok" and payload.get("ready") is True
        elif name == "market":
            ready = ready and payload.get("status") == "ok"
        return {
            "ready": bool(ready),
            "http_status": int(response.status_code),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except requests.RequestException as exc:
        return {
            "ready": False,
            "http_status": None,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error_class": type(exc).__name__,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=float, default=120)
    parser.add_argument("--interval-seconds", type=float, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=0.4)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT.relative_to(PROJECT_ROOT)))
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing health evidence: {output}")
    deadline = time.monotonic() + max(1.0, args.duration_seconds)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        while time.monotonic() < deadline:
            row = {
                "observed_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "monotonic_ns": time.monotonic_ns(),
                "services": {
                    name: _probe(name, url, max(0.1, args.timeout_seconds))
                    for name, url in ENDPOINTS.items()
                },
            }
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            time.sleep(max(0.1, args.interval_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
