from __future__ import annotations

"""Run one real local-Qwen candidate from a temporary canonical artifact.

The market snapshot is read from the configured canonical source.  All
orchestrator writes are redirected to a temporary SQLite database, and the
candidate is admitted only as offline/shadow work.  Raw prompts and outputs are
never printed.
"""

import argparse
import json
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect one sanitized Stage 7 canonical candidate execution."
    )
    parser.add_argument("--code", default="2330")
    parser.add_argument("--question", default="請分析價格、技術、事件與風險")
    args = parser.parse_args()
    load_line_bot_env()
    now = datetime.now(TPE).isoformat(timespec="seconds")
    with tempfile.TemporaryDirectory(prefix="stage7-canonical-") as directory:
        database = Path(directory) / "canonical-evidence.sqlite3"
        connection_factory = lambda: sqlite3.connect(database)  # noqa: E731
        artifact = run_canonical_analysis(
            code=str(args.code),
            delivery_channel="web",
            analysis_cutoff=now,
            request_received_at=now,
            profile="focused",
            connection_factory=connection_factory,
        )
        connection = sqlite3.connect(database)
        try:
            connection.row_factory = sqlite3.Row
            events = events_available_at_cutoff(connection, artifact["analysis_cutoff"])
        finally:
            connection.close()
        allowed_event_ids = {str(item) for item in artifact.get("event_ids") or []}
        packet = build_canonical_model_fact_packet_v2(
            artifact,
            requested_scopes=["price", "technical", "events", "risk"],
            event_records=[
                event for event in events if str(event.get("event_id") or "") in allowed_event_ids
            ],
        )
        result = run_canonical_model_candidate(
            str(args.question),
            packet,
            execution_mode="offline",
        )

    blockers = []
    if result.get("validator_result") != "pass":
        blockers.append("actual_canonical_candidate_validator_rejection")
    if result.get("preflight", {}).get("ready") is not True:
        blockers.append("canonical_candidate_preflight_rejection")
    evidence = {
        "evidence_contract": "Stage7CanonicalCandidateEvidenceV1",
        "source": "actual_local_model_execution",
        "synthetic_execution_count": 0,
        "actual_execution_count": 1,
        "production_database_write_count": 0,
        "temporary_artifact_database_destroyed": True,
        "candidate_reply_sent_to_web_or_line": False,
        "candidate_can_replace_stable": result.get("candidate_can_replace_stable"),
        "candidate_can_override_referee": result.get("candidate_can_override_referee"),
        "artifact": {
            "analysis_id": artifact.get("analysis_id"),
            "snapshot_id": artifact.get("snapshot_id"),
            "analysis_cutoff": artifact.get("analysis_cutoff"),
            "validity": artifact.get("validity"),
            "canonical_answer_text_hash": artifact.get("canonical_answer_text_hash"),
            "evidence_count": len(artifact.get("evidence_ids") or []),
            "event_count": len(artifact.get("event_ids") or []),
            "omission_count": len(artifact.get("omissions") or []),
            "conflict_count": len(artifact.get("conflicts") or []),
        },
        "candidate": {
            "candidate_version": result.get("candidate_version"),
            "model_id": result.get("model_id"),
            "profile": result.get("profile"),
            "decoding": result.get("decoding"),
            "packet_digest": result.get("packet_digest"),
            "compacted_packet_sha256": result.get("compacted_packet_sha256"),
            "preflight": result.get("preflight"),
            "prompt_token_count": result.get("prompt_token_count"),
            "completion_token_count": result.get("completion_token_count"),
            "queue_wait_ms": result.get("queue_wait_ms"),
            "model_latency_ms": result.get("model_latency_ms"),
            "total_duration_ms": result.get("total_duration_ms"),
            "finish_reason": result.get("finish_reason"),
            "validator_result": result.get("validator_result"),
            "validator_reason_codes": result.get("validator_reason_codes"),
            "raw_validator_result": result.get("raw_validator_result"),
            "raw_validator_reason_codes": result.get("raw_validator_reason_codes"),
            "ungrounded_claim_count": result.get("ungrounded_claim_count"),
            "referee_override_count": result.get("referee_override_count"),
            "model_output_sha256": result.get("model_output_sha256"),
            "model_output_characters": len(str(result.get("model_output") or "")),
        },
        "release_blockers": blockers,
        "release_ready": not blockers,
    }
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
