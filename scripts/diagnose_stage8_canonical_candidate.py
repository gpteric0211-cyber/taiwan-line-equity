from __future__ import annotations

"""Reproduce one public-market canonical candidate rejection without persistence."""

import json
import argparse
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", default="2330")
    parser.add_argument("--profile", choices=("focused", "comprehensive"), default="focused")
    parser.add_argument("--scopes", default="price,technical,events,risk")
    parser.add_argument("--question", default="請分析價格、技術、事件與風險")
    args = parser.parse_args()
    scopes = [item.strip() for item in str(args.scopes).split(",") if item.strip()]
    load_line_bot_env()
    now = datetime.now(TPE).isoformat(timespec="seconds")
    with tempfile.TemporaryDirectory(prefix="stage8-candidate-diagnostic-") as directory:
        database = Path(directory) / "artifact.sqlite3"
        factory = lambda: sqlite3.connect(database)  # noqa: E731
        artifact = run_canonical_analysis(
            code=str(args.code),
            delivery_channel="web",
            analysis_cutoff=now,
            request_received_at=now,
            profile=str(args.profile),
            connection_factory=factory,
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
            requested_scopes=scopes,
            event_records=[
                event for event in events if str(event.get("event_id") or "") in allowed_event_ids
            ],
        )
        result = run_canonical_model_candidate(
            str(args.question),
            packet,
            execution_mode="offline",
        )

    try:
        output = json.loads(str(result.get("model_output") or "{}"))
    except (TypeError, ValueError):
        output = {"invalid_json_output": str(result.get("model_output") or "")}
    used_ids = {
        str(evidence_id)
        for block in output.get("explanation_blocks") or []
        if isinstance(block, dict)
        for evidence_id in block.get("evidence_ids") or []
    }
    facts = {
        str(item.get("fact_id")): {
            "domain": item.get("domain"),
            "field": item.get("field"),
            "value": item.get("value"),
            "unit": item.get("unit"),
            "quality": item.get("quality"),
            "use_scope": item.get("use_scope"),
        }
        for item in result.get("compacted_packet", {}).get("facts") or []
        if str(item.get("fact_id") or "") in used_ids
    }
    event_contract = [
        {
            "event_id": item.get("event_id"),
            "verification_state": item.get("verification_state"),
            "allow_display": item.get("allow_display"),
            "citation_required": item.get("citation_required"),
            "source_url_present": bool(item.get("source_url")),
        }
        for item in result.get("compacted_packet", {}).get("events") or []
    ]
    diagnostic = {
        "contract": "Stage8CanonicalCandidateDiagnosticV1",
        "temporary_database_destroyed": True,
        "production_database_writes": 0,
        "candidate_can_replace_stable": result.get("candidate_can_replace_stable"),
        "packet_digest": result.get("packet_digest"),
        "validator_result": result.get("validator_result"),
        "validator_reason_codes": result.get("validator_reason_codes"),
        "ungrounded_claim_count": result.get("ungrounded_claim_count"),
        "referee_override_count": result.get("referee_override_count"),
        "model_output": output,
        "used_fact_contract": facts,
        "event_contract": event_contract,
        "render_contract": result.get("compacted_packet", {}).get("render_contract"),
    }
    # ASCII escaping keeps Traditional Chinese legible across Windows code pages.
    print(json.dumps(diagnostic, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
