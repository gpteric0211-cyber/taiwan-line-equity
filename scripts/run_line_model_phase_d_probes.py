from __future__ import annotations

"""Collect contention evidence; destructive cold reload is opt-in maintenance only."""

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, TextIO

import requests
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = PROJECT_ROOT / "review_src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.release_source_fingerprint import (  # noqa: E402
    RUNTIME_SOURCE_FINGERPRINT_CONTRACT,
    current_runtime_source_fingerprint,
)
from scripts import run_line_model_phase_d as phase_d  # noqa: E402


PRIVATE_ENV = PROJECT_ROOT / ".env.line_bot"
PROBES = (
    ("memory_compaction_contention", "memory-contention-probe"),
    ("vision_stable_reply_contention", "vision-stable-reply-probe"),
)
COLD_PROBE = ("cold_load", "cold-load-probe")


def _selected_probes(*, include_cold: bool, maintenance_window_confirmed: bool) -> tuple:
    if include_cold and not maintenance_window_confirmed:
        raise ValueError("--include-cold requires --maintenance-window-confirmed")
    return (COLD_PROBE, *PROBES) if include_cold else PROBES


def _journal(handle: TextIO, event: dict[str, Any]) -> None:
    handle.write(json.dumps({"observed_at": phase_d._now(), **event}, ensure_ascii=False) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def _probe_passed(item: dict[str, Any], *, name: str) -> bool:
    body = item.get("body")
    if item.get("http_status") != 200 or not isinstance(body, dict):
        return False
    if name == "cold_load":
        return (
            body.get("benchmark_contract") == "line-model-maintenance-cold-load-v2"
            and body.get("probe_status") == "pass"
            and body.get("resident_after") is True
            and body.get("recovery_required") is False
        )
    if name == "memory_compaction_contention":
        return body.get("maintenance_outcome") == "preempted"
    if name == "vision_stable_reply_contention":
        stable = body.get("stable_reply") or {}
        return (
            body.get("vision_outcome") == "completed"
            and isinstance(stable, dict)
            and stable.get("completed_within_internal_reply_budget") is True
            and stable.get("candidate_reply_submitted") is False
        )
    return False


def _cold_load_timeout(*, base_url: str, token: str) -> int:
    response = requests.get(
        f"{base_url}/cold-load-capability",
        headers={"Authorization": f"Bearer {token}"}, timeout=5,
    )
    try:
        capability = _safe_body(response)
        timeout = capability.get("load_timeout_seconds")
        if (
            response.status_code != 200
            or capability.get("benchmark_contract") != "line-model-maintenance-cold-load-v2"
            or capability.get("enabled") is not True
            or capability.get("maintenance_only") is not True
            or capability.get("exclusive_admission") is not True
            or type(timeout) is not int or not 30 <= timeout <= 3600
        ):
            raise ValueError("cold_probe_not_enabled_or_unsupported")
        return timeout
    finally:
        response.close()


def _collect_probes(
    selected: tuple, *, token: str, base_url: str, journal: TextIO
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    aborted = False
    for name, endpoint in selected:
        if aborted:
            results[name] = {"http_status": None, "skip_reason": "previous_probe_failed"}
            _journal(journal, {"probe": name, "state": "skipped", **results[name]})
            continue
        _journal(journal, {"probe": name, "state": "started"})
        started = time.monotonic()
        response = None
        try:
            cold_timeout = _cold_load_timeout(base_url=base_url, token=token) if name == "cold_load" else 0
            response = requests.post(
                f"{base_url}/{endpoint}",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "acknowledge_possible_interactive_delay": True,
                    "maintenance_window_confirmed": name == "cold_load",
                },
                timeout=(5, cold_timeout + 90 if name == "cold_load" else 240),
            )
            result = {"http_status": response.status_code, "body": _safe_body(response)}
        except requests.RequestException as exc:
            # Error text may contain URLs or credentials; retain only its class.
            result = {"http_status": None, "transport_error_class": type(exc).__name__}
        except ValueError:
            result = {"http_status": None, "reason_code": "cold_probe_not_enabled_or_unsupported"}
        finally:
            if response is not None:
                response.close()
        result["http_round_trip_ms"] = int((time.monotonic() - started) * 1000)
        results[name] = result
        _journal(journal, {"probe": name, "state": "finished", **result})
        print(json.dumps({"probe": name, "http_status": result["http_status"]}), flush=True)
        aborted = not _probe_passed(result, name=name)
    return results


def _safe_body(response: requests.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError:
        return {"non_json_sha256": hashlib.sha256(response.content).hexdigest()}
    return value if isinstance(value, dict) else {"invalid_body_type": type(value).__name__}


def _runtime_source_preflight(*, token: str, base_url: str) -> dict[str, Any]:
    current = current_runtime_source_fingerprint()
    response = requests.get(
        f"{base_url}/source-fingerprint",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    try:
        remote = _safe_body(response)
    finally:
        response.close()
    reasons: list[str] = []
    if response.status_code != 200:
        reasons.append("runtime_source_fingerprint_endpoint_unavailable")
    if remote.get("contract_version") != RUNTIME_SOURCE_FINGERPRINT_CONTRACT:
        reasons.append("runtime_source_fingerprint_contract_mismatch")
    if remote.get("complete") is not True or current.get("complete") is not True:
        reasons.append("runtime_source_fingerprint_incomplete")
    if remote.get("source_digest") != current.get("source_digest"):
        reasons.append("runtime_source_digest_mismatch")
    if remote.get("source_hashes") != current.get("source_hashes"):
        reasons.append("runtime_source_hashes_mismatch")
    return {
        "contract_version": RUNTIME_SOURCE_FINGERPRINT_CONTRACT,
        "http_status": int(response.status_code),
        "remote_loaded_at": remote.get("captured_at"),
        "remote_source_digest": remote.get("source_digest"),
        "current_source_digest": current.get("source_digest"),
        "matches": not reasons,
        "reason_codes": reasons or ["pass"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-driver", default="")
    parser.add_argument("--include-cold", action="store_true")
    parser.add_argument("--maintenance-window-confirmed", action="store_true")
    args = parser.parse_args()
    try:
        selected = _selected_probes(
            include_cold=args.include_cold,
            maintenance_window_confirmed=args.maintenance_window_confirmed,
        )
    except ValueError as exc:
        parser.error(str(exc))
    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    if output.exists():
        raise FileExistsError(f"refusing to overwrite Phase D probe evidence: {output}")
    health_output = output.with_name(f"{output.stem}_health.jsonl")
    if health_output.exists():
        raise FileExistsError(f"refusing to overwrite Phase D probe health evidence: {health_output}")
    journal_output = output.with_name(f"{output.stem}_attempts.jsonl")
    if journal_output.exists():
        raise FileExistsError(f"refusing to overwrite Phase D probe journal: {journal_output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    values = dotenv_values(PRIVATE_ENV)
    token = str(values.get("BOT_MARKET_DATA_TOKEN") or "")
    if len(token) < 32:
        raise RuntimeError("persistent BOT_MARKET_DATA_TOKEN is unavailable")
    os.environ.update({key: str(value) for key, value in values.items() if value is not None})
    port = str(os.getenv("LINE_GATEWAY_PORT") or "8021")
    base_url = f"http://127.0.0.1:{port}/internal/line-model-benchmark"
    runtime_source_preflight = _runtime_source_preflight(token=token, base_url=base_url)
    if runtime_source_preflight["matches"] is not True:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "reason_code": "runtime_source_fingerprint_mismatch",
                    "runtime_source_preflight": runtime_source_preflight,
                    "model_probe_posts": 0,
                },
                ensure_ascii=False,
            )
        )
        return 2
    health_before = phase_d._service_health()
    deployment, model_profile, environment_match = phase_d._environment_snapshot(args.expected_driver)
    results: dict[str, Any] = {}
    health_stop = threading.Event()
    health_thread = threading.Thread(
        target=phase_d._health_sampler,
        args=(health_stop, health_output),
        name="phase-d-probe-health-sampler",
        daemon=True,
    )
    health_thread.start()
    collector_error_class = None
    try:
        with journal_output.open("x", encoding="utf-8", newline="\n") as journal:
            results = _collect_probes(
                selected, token=token, base_url=base_url, journal=journal,
            )
    except Exception as exc:
        collector_error_class = type(exc).__name__
    finally:
        health_stop.set()
        health_thread.join(timeout=3)
    try:
        health_after = phase_d._service_health()
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        health_after = {"error_class": type(exc).__name__}
    health_evidence = phase_d._health_evidence_summary(health_output)
    successful = (
        len(results) == len(selected)
        and all(_probe_passed(item, name=name) for name, item in results.items())
        and health_evidence["all_services_ready"]
        and "error_class" not in health_after
        and collector_error_class is None
    )
    artifact = {
        "contract_version": "phase-d-contention-evidence-v2",
        "captured_at": phase_d._now(),
        "deployment_target": deployment,
        "environment_match": environment_match,
        "model_profile": model_profile,
        "source_hashes": {
            relative: phase_d._sha256(PROJECT_ROOT / relative)
            for relative in (*phase_d.SOURCE_BINDINGS, "scripts/run_line_model_phase_d_probes.py")
        },
        "runtime_source_preflight": runtime_source_preflight,
        "health_before": health_before,
        "health_after": health_after,
        "health_evidence": health_evidence,
        "attempt_journal": journal_output.name,
        "collector_error_class": collector_error_class,
        "probes": results,
        "requested_probe_set_complete": successful,
        "cold_probe_requested": args.include_cold,
        "skipped_probes": {} if args.include_cold else {"cold_load": "maintenance_not_requested"},
        "probe_set_complete": successful and args.include_cold,
        "valid_for_full_load_matrix": False,
        "remaining_cases": [
            "news cache hit and miss",
            "multi-event webhook 5 and 20",
            "reply-network delay",
            "actual webhook ingress to LINE reply telemetry",
        ],
    }
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "probe_set_complete": successful}, ensure_ascii=False))
    return 0 if successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
