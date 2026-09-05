from __future__ import annotations

import json
import sys
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.line_reply_telemetry_service import append_line_reply_telemetry  # noqa: E402


def test_reply_telemetry_is_append_only_and_drops_sensitive_fields(monkeypatch, tmp_path: Path) -> None:
    destination = tmp_path / "reply-telemetry.jsonl"
    monkeypatch.setenv("LINE_REPLY_TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("LINE_REPLY_TELEMETRY_PATH", str(destination))
    record = {
        "correlation": "abc123",
        "message_type": "text",
        "reply_status": "sent",
        "webhook_ingress_to_reply_ms": 321,
        "handler_start_to_reply_ms": 300,
        "line_send_ms": 20,
        "answer_utf8_bytes": 99,
        "stable_reply_sha256": "f" * 64,
        "gpu_active_category_at_handler_start": "shadow_candidate",
        "gpu_queue_depth_at_handler_start": 0,
        "shadow_active_at_handler_start": True,
        "candidate_authorized": True,
        "candidate_selected": True,
        "candidate_delivered": True,
        "candidate_reason": "delivered",
        "candidate_error_class": None,
        "canary_percentage": 5,
        "cohort_bucket": 123,
        "authorization_id": "auth-1",
        "analysis_id": "analysis-1",
        "candidate_reply_sha256": "e" * 64,
        "user_id": "must-not-be-written",
        "reply_token": "must-not-be-written",
        "message_text": "must-not-be-written",
        "raw_candidate": "must-not-be-written",
        "model_packet": {"secret": "must-not-be-written"},
    }

    append_line_reply_telemetry(record)
    append_line_reply_telemetry({**record, "correlation": "def456"})

    lines = destination.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["schema_version"] == "line-reply-telemetry-v2"
    assert first["shadow_active_at_handler_start"] is True
    assert first["candidate_delivered"] is True
    assert first["candidate_reply_sha256"] == "e" * 64
    assert second["correlation"] == "def456"
    for sensitive in ("user_id", "reply_token", "message_text", "raw_candidate", "model_packet"):
        assert sensitive not in first
