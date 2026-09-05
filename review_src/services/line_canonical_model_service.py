from __future__ import annotations

"""Post-reply canonical candidate preparation for LINE shadow evidence."""

import hashlib
import json
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from adapter.bot_market_data_client import fetch_canonical_question_model_packet
from adapter.qwen_local import qwen_model_resident
from core.line_bot_config import env_int, env_text
from core.line_model_release_config import (
    configured_release_rollout,
    release_authorization_status,
)
from services.canonical_model_candidate_service import run_canonical_model_candidate
from services.line_model_warmup_service import ensure_background_text_model_warmup
from services.line_request_planning_service import plan_line_request


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TPE = ZoneInfo("Asia/Taipei")
CANONICAL_LINE_REQUEST_VERSION = "CanonicalLineCandidateRequestV2"
CANONICAL_LINE_SHADOW_EVIDENCE_VERSION = "CanonicalLineShadowEvidenceV2"
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="line-canonical-shadow")
_PENDING_LIMIT = threading.BoundedSemaphore(16)
_EVIDENCE_LOCK = threading.Lock()


def line_canonical_model_readiness() -> dict[str, Any]:
    rollout = configured_release_rollout()
    authorization = release_authorization_status()
    return {
        "contract_version": CANONICAL_LINE_REQUEST_VERSION,
        "rollout": rollout,
        "shadow_enabled": rollout == "shadow",
        "canary_configured": rollout == "canary",
        "candidate_calls_enabled": rollout in {"shadow", "canary"},
        "candidate_can_replace_reply": False,
        "candidate_delivery_integration": "complete_fail_closed_parity_guarded",
        "candidate_replacement_gate": (
            "authorized_backend_revalidation_and_immutable_model_answer_persistence"
        ),
        "canonical_model_answer_persistence": "implemented_fail_closed",
        "candidate_execution_timing": (
            "before_reply_for_selected_canary_after_reply_for_shadow"
        ),
        "packet_source": "sealed_canonical_analysis_artifact_via_authenticated_post",
        "legacy_projection_used_for_candidate": False,
        "release_authorization": authorization,
        "raw_request_or_packet_persisted": False,
    }


def _evidence_path() -> Path:
    configured = env_text("LINE_CANONICAL_V3_EVIDENCE_PATH").strip()
    path = Path(configured) if configured else Path(
        "logs/line_model_shadow/canonical_line_shadow.jsonl"
    )
    return path if path.is_absolute() else PROJECT_ROOT / path


def _append_evidence(record: dict[str, Any], path: Path | None = None) -> None:
    destination = path or _evidence_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with _EVIDENCE_LOCK:
        with destination.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered + "\n")


def prepare_line_canonical_candidate(
    *,
    question: str,
    model_facts: dict[str, Any],
    focus: str,
    conversation_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Capture a point-in-time in-memory request without calling the market API."""

    if configured_release_rollout() not in {"shadow", "canary"}:
        return None
    stock = model_facts.get("stock") if isinstance(model_facts.get("stock"), dict) else {}
    code = str(model_facts.get("code") or stock.get("code") or "").strip()
    plan = plan_line_request(str(question), focus=str(focus), has_stock=bool(code))
    execution = plan.get("execution_plan") if isinstance(plan.get("execution_plan"), dict) else {}
    scopes = [str(item) for item in execution.get("effective_scopes") or []]
    if not code or not scopes:
        return None
    captured_at = datetime.now(TPE).isoformat(timespec="seconds")
    return {
        "contract_version": CANONICAL_LINE_REQUEST_VERSION,
        "request_id": uuid.uuid4().hex,
        "question": str(question),
        "conversation_context": dict(conversation_context or {}),
        "requested_scopes": scopes,
        "profile": (
            "comprehensive"
            if execution.get("effective_depth") == "comprehensive"
            else "focused"
        ),
        "analysis_cutoff": captured_at,
        "request_received_at": captured_at,
        "stock_code_hint": code,
        "raw_request_persisted": False,
    }


def _sanitized_result(
    request: dict[str, Any],
    stable_reply_sha256: str,
    *,
    packet_fetcher: Callable[..., dict[str, Any]],
    candidate_runner: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    envelope = packet_fetcher(
        str(request["question"]),
        requested_scopes=list(request["requested_scopes"]),
        conversation_context=dict(request.get("conversation_context") or {}),
        analysis_cutoff=str(request["analysis_cutoff"]),
        request_received_at=str(request["request_received_at"]),
        profile=str(request["profile"]),
    )
    if envelope.get("packet_ready") is not True or not isinstance(envelope.get("packet"), dict):
        return {
            "evidence_version": CANONICAL_LINE_SHADOW_EVIDENCE_VERSION,
            "request_id": request.get("request_id"),
            "status": "packet_not_ready",
            "reason_codes": list(envelope.get("reason_codes") or ["canonical_packet_not_ready"]),
            "stable_reply_sha256": stable_reply_sha256,
            "candidate_reply_sent": False,
            "raw_request_or_packet_persisted": False,
        }
    candidate = candidate_runner(
        str(request["question"]),
        envelope["packet"],
        execution_mode="shadow",
    )
    return {
        "evidence_version": CANONICAL_LINE_SHADOW_EVIDENCE_VERSION,
        "request_id": request.get("request_id"),
        "status": "completed" if candidate.get("validator_result") == "pass" else "rejected",
        "captured_at": datetime.now(TPE).isoformat(timespec="seconds"),
        "analysis_id": envelope.get("analysis_id"),
        "snapshot_id": envelope.get("snapshot_id"),
        "analysis_cutoff": envelope.get("analysis_cutoff"),
        "canonical_answer_text_hash": envelope.get("canonical_answer_text_hash"),
        "packet_digest": envelope.get("packet_digest"),
        "stable_reply_sha256": stable_reply_sha256,
        "candidate_model_output_sha256": candidate.get("model_output_sha256"),
        "profile": candidate.get("profile"),
        "packet_token_count": candidate.get("packet_token_count"),
        "estimated_prompt_token_count": candidate.get("estimated_prompt_token_count"),
        "finish_reason": candidate.get("finish_reason"),
        "validator_result": candidate.get("validator_result"),
        "validator_reason_codes": list(candidate.get("validator_reason_codes") or []),
        "ungrounded_claim_count": int(candidate.get("ungrounded_claim_count") or 0),
        "referee_override_count": int(candidate.get("referee_override_count") or 0),
        "queue_wait_ms": int(candidate.get("queue_wait_ms") or 0),
        "model_latency_ms": int(candidate.get("model_latency_ms") or 0),
        "total_duration_ms": int(candidate.get("total_duration_ms") or 0),
        "error_classification": candidate.get("error_classification"),
        "candidate_reply_sent": False,
        "candidate_can_replace_stable": False,
        "canonical_artifact_write_authorized_by_post_use_case": True,
        "raw_request_or_packet_persisted": False,
    }


def submit_line_canonical_shadow(
    request: dict[str, Any],
    *,
    stable_reply_sha256: str = "",
    evidence_path: Path | None = None,
    packet_fetcher: Callable[..., dict[str, Any]] = fetch_canonical_question_model_packet,
    candidate_runner: Callable[..., dict[str, Any]] = run_canonical_model_candidate,
) -> Future[dict[str, Any]] | None:
    """Return immediately; retrieval and candidate generation happen after LINE reply."""

    if configured_release_rollout() not in {"shadow", "canary"}:
        return None
    if request.get("contract_version") != CANONICAL_LINE_REQUEST_VERSION:
        return None
    if not qwen_model_resident():
        ensure_background_text_model_warmup()
        return None
    if not _PENDING_LIMIT.acquire(blocking=False):
        return None

    def run() -> dict[str, Any]:
        try:
            try:
                result = _sanitized_result(
                    request,
                    str(stable_reply_sha256),
                    packet_fetcher=packet_fetcher,
                    candidate_runner=candidate_runner,
                )
            except Exception as exc:
                result = {
                    "evidence_version": CANONICAL_LINE_SHADOW_EVIDENCE_VERSION,
                    "request_id": request.get("request_id"),
                    "status": "failed",
                    "error_class": type(exc).__name__,
                    "stable_reply_sha256": str(stable_reply_sha256),
                    "candidate_reply_sent": False,
                    "raw_request_or_packet_persisted": False,
                }
            _append_evidence(result, evidence_path)
            return result
        finally:
            _PENDING_LIMIT.release()

    return _EXECUTOR.submit(run)
