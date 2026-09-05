from __future__ import annotations

"""Deidentified append-only telemetry for real LINE reply latency evidence."""

import json
import threading
from pathlib import Path
from typing import Any

from core.line_bot_config import PROJECT_ROOT, env_bool, env_text
from core.utils import now_tpe


_WRITE_LOCK = threading.Lock()


def _telemetry_path() -> Path:
    configured = env_text("LINE_REPLY_TELEMETRY_PATH")
    path = Path(configured).expanduser() if configured else Path("logs/line_model_shadow/line_reply_telemetry.jsonl")
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def append_line_reply_telemetry(record: dict[str, Any]) -> Path | None:
    """Append one record without storing message text, identity, or reply tokens."""

    if not env_bool("LINE_REPLY_TELEMETRY_ENABLED", True):
        return None

    allowed = {
        "correlation",
        "message_type",
        "reply_status",
        "error_class",
        "webhook_ingress_to_reply_ms",
        "handler_start_to_reply_ms",
        "line_send_ms",
        "answer_utf8_bytes",
        "stable_reply_sha256",
        "gpu_active_category_at_handler_start",
        "gpu_queue_depth_at_handler_start",
        "shadow_active_at_handler_start",
        "answer_path",
        "candidate_authorized",
        "candidate_selected",
        "candidate_delivered",
        "candidate_reason",
        "candidate_error_class",
        "canary_percentage",
        "cohort_bucket",
        "authorization_id",
        "analysis_id",
        "candidate_reply_sha256",
    }
    payload = {
        "schema_version": "line-reply-telemetry-v2",
        "captured_at": now_tpe().isoformat(),
        **{key: record.get(key) for key in sorted(allowed)},
    }
    path = _telemetry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
    return path
