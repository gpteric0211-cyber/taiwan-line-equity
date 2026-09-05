from __future__ import annotations

"""Collect actual in-process canonical candidate concurrency evidence."""

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = PROJECT_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.line_bot_config import load_line_bot_env  # noqa: E402
from repository.single_track_v3_repository import events_available_at_cutoff  # noqa: E402
from services.canonical_analysis_orchestrator import run_canonical_analysis  # noqa: E402
from services.canonical_model_candidate_service import (  # noqa: E402
    run_canonical_model_candidate,
)
from services.canonical_model_packet_service import (  # noqa: E402
    build_canonical_model_fact_packet_v2,
)


TPE = ZoneInfo("Asia/Taipei")
LEVELS = (1, 2, 4, 8)
PROFILE_CASES = {
    "focused": {
        "scopes": ["price", "technical", "events", "risk"],
        "question": "請分析價格、技術、事件與風險",
        "latency_limit_ms": 31_000,
    },
    "comprehensive": {
        "scopes": ["fundamentals", "institutional", "technical", "current_news"],
        "question": "請完整分析基本面、籌碼、技術面與最新新聞",
        "latency_limit_ms": 45_000,
    },
}


def _nearest_rank(values: list[int], proportion: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * proportion) - 1)]


def _packet(profile: str) -> dict[str, Any]:
    case = PROFILE_CASES[profile]
    now = datetime.now(TPE).isoformat(timespec="seconds")
    with tempfile.TemporaryDirectory(prefix=f"stage8-{profile}-matrix-") as directory:
        database = Path(directory) / "artifact.sqlite3"
        factory = lambda: sqlite3.connect(database)  # noqa: E731
        artifact = run_canonical_analysis(
            code="2330",
            delivery_channel="web",
            analysis_cutoff=now,
            request_received_at=now,
            profile=profile,
            connection_factory=factory,
        )
        connection = sqlite3.connect(database)
        try:
            connection.row_factory = sqlite3.Row
            events = events_available_at_cutoff(connection, artifact["analysis_cutoff"])
        finally:
            connection.close()
        allowed = {str(item) for item in artifact.get("event_ids") or []}
        return build_canonical_model_fact_packet_v2(
            artifact,
            requested_scopes=case["scopes"],
            event_records=[
                event for event in events if str(event.get("event_id") or "") in allowed
            ],
        )


def _sample(profile: str, packet: dict[str, Any]) -> dict[str, Any]:
    result = run_canonical_model_candidate(
        str(PROFILE_CASES[profile]["question"]),
        packet,
        execution_mode="offline",
    )
    return {
        "validator_result": result.get("validator_result"),
        "validator_reason_codes": result.get("validator_reason_codes"),
        "ungrounded_claim_count": result.get("ungrounded_claim_count"),
        "referee_override_count": result.get("referee_override_count"),
        "queue_wait_ms": result.get("queue_wait_ms"),
        "model_latency_ms": result.get("model_latency_ms"),
        "total_duration_ms": result.get("total_duration_ms"),
        "prompt_token_count": result.get("prompt_token_count"),
        "completion_token_count": result.get("completion_token_count"),
        "finish_reason": result.get("finish_reason"),
        "model_output_sha256": result.get("model_output_sha256"),
        "candidate_can_replace_stable": result.get("candidate_can_replace_stable"),
    }


def _level(profile: str, packet: dict[str, Any], concurrency: int) -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(_sample, profile, packet) for _ in range(concurrency)]
        samples = [future.result() for future in as_completed(futures)]
    totals = [int(item["queue_wait_ms"] or 0) + int(item["total_duration_ms"] or 0) for item in samples]
    return {
        "concurrency": concurrency,
        "actual_execution_count": len(samples),
        "validator_passed": sum(item["validator_result"] == "pass" for item in samples),
        "validator_rejected": sum(item["validator_result"] != "pass" for item in samples),
        "queue_plus_execution_p95_ms": _nearest_rank(totals, 0.95),
        "queue_wait_p95_ms": _nearest_rank(
            [int(item["queue_wait_ms"] or 0) for item in samples],
            0.95,
        ),
        "samples": samples,
    }


def main(output_path: Path | None = None) -> int:
    load_line_bot_env()
    profiles = {}
    for profile in PROFILE_CASES:
        packet = _packet(profile)
        levels = [_level(profile, packet, level) for level in LEVELS]
        profiles[profile] = {
            "packet_digest": packet["packet_digest"],
            "latency_limit_ms": PROFILE_CASES[profile]["latency_limit_ms"],
            "levels": levels,
            "actual_execution_count": sum(item["actual_execution_count"] for item in levels),
            "validator_passed": sum(item["validator_passed"] for item in levels),
            "validator_rejected": sum(item["validator_rejected"] for item in levels),
            "latency_gate_passed": all(
                int(item["queue_plus_execution_p95_ms"] or 0)
                < int(PROFILE_CASES[profile]["latency_limit_ms"])
                for item in levels
            ),
        }
    evidence = {
        "evidence_contract": "Stage8CanonicalCandidateMatrixV1",
        "captured_at": datetime.now(TPE).isoformat(timespec="seconds"),
        "collector": "scripts/collect_stage8_canonical_candidate_matrix.py",
        "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source": "actual_local_hardware_controlled_replay",
        "synthetic_execution_count": 0,
        "production_database_write_count": 0,
        "candidate_reply_sent_to_web_or_line": False,
        "profiles": profiles,
        "actual_execution_count": sum(item["actual_execution_count"] for item in profiles.values()),
        "validator_passed": sum(item["validator_passed"] for item in profiles.values()),
        "validator_rejected": sum(item["validator_rejected"] for item in profiles.values()),
        "phase_d_qualified": False,
        "phase_d_blockers": [
            "five_trading_days_not_met",
            "one_hundred_executions_per_profile_not_met",
        ],
    }
    evidence["evidence_sha256"] = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    rendered = json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if output_path is None:
        print(rendered, end="")
    else:
        destination = output_path if output_path.is_absolute() else PROJECT_ROOT / output_path
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            ) as handle:
                handle.write(rendered)
                handle.flush()
                temporary_path = Path(handle.name)
            temporary_path.replace(destination)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        print(
            json.dumps(
                {
                    "output": str(output_path),
                    "evidence_sha256": evidence["evidence_sha256"],
                    "actual_execution_count": evidence["actual_execution_count"],
                    "validator_rejected": evidence["validator_rejected"],
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="Atomically replace an evidence path relative to the project root.",
    )
    cli_args = parser.parse_args()
    raise SystemExit(main(cli_args.output))
