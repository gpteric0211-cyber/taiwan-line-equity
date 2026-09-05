from __future__ import annotations

"""Fixed, authenticated live workloads for LINE model acceptance evidence."""

import base64
import hashlib
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from adapter.bot_market_data_client import (
    fetch_canonical_question_model_packet,
    fetch_daily_market_data,
)
from adapter.qwen_local import (
    QwenClientError,
    qwen_chat,
    qwen_model_resident,
    qwen_preload_text_model,
    qwen_unload_text_model,
    qwen_vision_json,
)
from core.line_bot_config import env_bool, env_int, env_text
from services.line_bot_service import (
    _answer_stock_question_result,
    build_line_model_fact_projection,
)
from services.canonical_model_candidate_service import run_canonical_model_candidate
from services.line_canonical_model_service import prepare_line_canonical_candidate
from services.line_model_candidate_reply_service import (
    CandidateReplyRenderError,
    render_validated_candidate_reply_preview,
)
from services.line_model_research_service import (
    clear_line_model_research_cache,
    enrich_shadow_model_facts_with_research,
)
from services.line_model_shadow_service import submit_line_model_shadow
from services.line_model_warmup_service import (
    background_text_model_warmup_status,
    ensure_background_text_model_warmup,
)
from services.line_request_planning_service import plan_line_request
from services.model_admission_service import (
    ModelAdmissionError,
    model_admission_snapshot,
    run_interactive_model,
    run_maintenance_model,
    submit_maintenance_model,
)


BENCHMARK_QUESTIONS = {
    "focused_valuation": "台積電本益比與估值資料怎麼看",
    "focused_technical": "台積電技術面怎麼看",
    "fundamental_chip_technical_news": "台積電基本面、籌碼、技術面與最新新聞完整分析",
    "technical_valuation_support_risk": "台積電技術面、估值、支撐壓力與風險完整評估",
    "chip_night_us_events": "台積電籌碼、夜盤、美股與重大事件完整分析",
}
_VISION_PROBE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class LineModelBenchmarkError(RuntimeError):
    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _benchmark_job(*, scenario: str, code: str) -> tuple[dict[str, Any], str]:
    question = BENCHMARK_QUESTIONS.get(str(scenario))
    if not question:
        raise LineModelBenchmarkError("unknown benchmark scenario", reason_code="unknown_scenario")
    normalized_code = str(code or "").strip()
    if not normalized_code.isdigit() or len(normalized_code) not in {4, 5, 6}:
        raise LineModelBenchmarkError("invalid benchmark stock code", reason_code="invalid_code")
    retrieval_started = time.perf_counter_ns()
    payload = dict(fetch_daily_market_data(normalized_code, analysis_mode="close_batch") or {})
    retrieval_ms = round((time.perf_counter_ns() - retrieval_started) / 1_000_000, 3)
    payload["_line_question"] = question
    projection_started = time.perf_counter_ns()
    projection = build_line_model_fact_projection(payload)
    projection_ms = round((time.perf_counter_ns() - projection_started) / 1_000_000, 3)
    return {
        "request_id": uuid.uuid4().hex,
        "question": question,
        "model_facts": projection,
        "focus": "overview",
        "_benchmark_stage_timings_ms": {
            "retrieval": retrieval_ms,
            "projection": projection_ms,
        },
    }, question


def _require_resident_model() -> None:
    if not qwen_model_resident():
        raise LineModelBenchmarkError(
            "text model is not resident; live shadow benchmark is deferred",
            reason_code="model_not_resident",
        )


def control_live_research_cache(
    *,
    scenario: str,
    code: str = "2330",
    mode: str,
) -> dict[str, Any]:
    """Clear or prime the real process-local cache for one fixed benchmark scenario."""

    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {"clear", "prime"}:
        raise LineModelBenchmarkError(
            "research cache mode must be clear or prime",
            reason_code="invalid_research_cache_mode",
        )
    job, question = _benchmark_job(scenario=scenario, code=code)
    plan = plan_line_request(question, focus="overview", has_stock=True)
    execution_plan = plan.get("execution_plan") if isinstance(plan.get("execution_plan"), dict) else {}
    scopes = [str(item) for item in execution_plan.get("effective_scopes") or []]
    if "current_news" not in scopes and "geopolitics" not in scopes:
        raise LineModelBenchmarkError(
            "fixed scenario does not request controlled research",
            reason_code="research_not_requested",
        )
    cleared_entries = clear_line_model_research_cache()
    response: dict[str, Any] = {
        "benchmark_contract": "line-model-research-cache-control-v1",
        "scenario": scenario,
        "mode": normalized_mode,
        "cleared_entries": cleared_entries,
        "raw_query_persisted": False,
        "canonical_table_writes": 0,
    }
    if normalized_mode == "prime":
        research = enrich_shadow_model_facts_with_research(
            question=question,
            model_facts=dict(job["model_facts"]),
            requested_scopes=scopes,
        )
        summary = research.get("summary") if isinstance(research.get("summary"), dict) else {}
        response["prime_summary"] = summary
        response["primed"] = summary.get("cache_state") == "miss"
    else:
        response["primed"] = False
    return response


def run_live_shadow_sample(*, scenario: str, code: str = "2330") -> dict[str, Any]:
    """Run one sanitized final canonical-path sample through live GPU admission."""

    return _execute_live_canonical_shadow_sample(scenario=scenario, code=code)["response"]


def _execute_live_canonical_shadow_sample(
    *,
    scenario: str,
    code: str = "2330",
) -> dict[str, Any]:
    benchmark_started = time.monotonic()
    _require_resident_model()
    question = BENCHMARK_QUESTIONS.get(str(scenario))
    if not question:
        raise LineModelBenchmarkError("unknown benchmark scenario", reason_code="unknown_scenario")
    normalized_code = str(code or "").strip()
    if not normalized_code.isdigit() or len(normalized_code) not in {4, 5, 6}:
        raise LineModelBenchmarkError("invalid benchmark stock code", reason_code="invalid_code")
    request = prepare_line_canonical_candidate(
        question=question,
        model_facts={"code": normalized_code, "stock": {"code": normalized_code}},
        focus="overview",
        conversation_context=None,
    )
    if not isinstance(request, dict):
        raise LineModelBenchmarkError(
            "canonical shadow request is not enabled",
            reason_code="canonical_shadow_not_enabled",
        )
    packet_fetch_started = time.perf_counter_ns()
    envelope = fetch_canonical_question_model_packet(
        str(request["question"]),
        requested_scopes=list(request["requested_scopes"]),
        conversation_context=dict(request.get("conversation_context") or {}),
        analysis_cutoff=str(request["analysis_cutoff"]),
        request_received_at=str(request["request_received_at"]),
        profile=str(request["profile"]),
    )
    packet_fetch_ms = round(
        (time.perf_counter_ns() - packet_fetch_started) / 1_000_000,
        3,
    )
    if envelope.get("packet_ready") is not True or not isinstance(envelope.get("packet"), dict):
        raise LineModelBenchmarkError(
            "canonical packet is not ready",
            reason_code="canonical_packet_not_ready",
        )
    try:
        candidate = run_canonical_model_candidate(
            question,
            envelope["packet"],
            execution_mode="shadow",
        )
    except ModelAdmissionError as exc:
        raise LineModelBenchmarkError(str(exc), reason_code=exc.reason_code) from exc

    render_started = time.perf_counter_ns()
    preview: dict[str, Any] | None = None
    preview_rejection_reason: str | None = None
    if candidate.get("validator_result") == "pass":
        try:
            preview = render_validated_candidate_reply_preview(
                candidate,
                stock_code=normalized_code,
                stock_name="台積電" if normalized_code == "2330" else normalized_code,
            )
        except CandidateReplyRenderError as exc:
            preview_rejection_reason = exc.reason_code
    render_ms = round((time.perf_counter_ns() - render_started) / 1_000_000, 3)
    envelope_timings = dict(envelope.get("stage_timings_ms") or {})
    canonical_events = [
        item
        for item in envelope["packet"].get("events") or []
        if isinstance(item, dict)
    ]
    canonical_news_event_count = sum(
        1 for item in canonical_events if item.get("source_class") == "news_radar"
    )
    candidate_timings = dict(candidate.get("stage_timings_ms") or {})
    stage_timings = {
        "classification": envelope_timings.get("classification"),
        "retrieval": envelope_timings.get("retrieval", packet_fetch_ms),
        "projection": envelope_timings.get("projection"),
        "packet_build": round(
            float(envelope_timings.get("packet_build") or 0)
            + float(candidate_timings.get("packet_build") or 0),
            3,
        ),
        "generation": candidate_timings.get("generation"),
        "validation": candidate_timings.get("validation"),
        "render": render_ms,
    }
    finish_reason = str(candidate.get("finish_reason") or "")
    sanitized_result = {
        "candidate_version": candidate.get("candidate_version"),
        "execution_mode": candidate.get("execution_mode"),
        "candidate_model_called": finish_reason not in {"", "preflight_rejected"},
        "candidate_can_replace_stable": False,
        "candidate_can_override_referee": False,
        "depth": candidate.get("depth"),
        "scopes": list(candidate.get("scopes") or []),
        "generation_schema_version": candidate.get("generation_schema_version"),
        "generation_schema_sha256": candidate.get("generation_schema_sha256"),
        "generation_prompt_sha256": candidate.get("generation_prompt_sha256"),
        "analysis_id": envelope.get("analysis_id"),
        "snapshot_id": envelope.get("snapshot_id"),
        "canonical_answer_text_hash": envelope.get("canonical_answer_text_hash"),
        "artifact_reused": envelope.get("artifact_reused"),
        "canonical_artifact_cache_state": (
            "hit" if envelope.get("artifact_reused") is True else "miss"
        ),
        "event_record_count": envelope.get("event_record_count"),
        "canonical_news_event_count": canonical_news_event_count,
        "canonical_news_state": "hit" if canonical_news_event_count else "miss",
        "profile": candidate.get("profile"),
        "packet_token_count": candidate.get("packet_token_count"),
        "estimated_prompt_token_count": candidate.get("estimated_prompt_token_count"),
        "prompt_token_count": candidate.get("prompt_token_count"),
        "completion_token_count": candidate.get("completion_token_count"),
        "model_latency_ms": candidate.get("model_latency_ms"),
        "total_duration_ms": candidate.get("total_duration_ms"),
        "finish_reason": finish_reason,
        "validator_result": candidate.get("validator_result"),
        "validator_reason_codes": list(candidate.get("validator_reason_codes") or []),
        "ungrounded_claim_count": int(candidate.get("ungrounded_claim_count") or 0),
        "referee_override_count": int(candidate.get("referee_override_count") or 0),
        "error_classification": candidate.get("error_classification"),
        "model_output_sha256": candidate.get("model_output_sha256"),
        "preview_rendered": preview is not None,
        "preview_rejection_reason": preview_rejection_reason,
        "stage_timings_ms": stage_timings,
        "raw_candidate_or_packet_returned": False,
    }
    response = {
        "benchmark_contract": "line-model-live-canonical-shadow-sample-v2",
        "scenario": scenario,
        "question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "request_id": uuid.uuid4().hex,
        "queue_wait_ms": int(candidate.get("queue_wait_ms") or 0),
        "execution_ms": int(candidate.get("total_duration_ms") or 0),
        "packet_fetch_round_trip_ms": packet_fetch_ms,
        "benchmark_end_to_end_ms": int((time.monotonic() - benchmark_started) * 1000),
        "result": sanitized_result,
        "admission_after": model_admission_snapshot(),
    }
    return {"response": response, "candidate": candidate, "preview": preview}


def run_live_candidate_reply_preview(*, scenario: str, code: str = "2330") -> dict[str, Any]:
    """Run one shadow candidate and render it for human review only."""

    execution = _execute_live_canonical_shadow_sample(scenario=scenario, code=code)
    sample = execution["response"]
    result = sample.get("result") if isinstance(sample.get("result"), dict) else {}
    response: dict[str, Any] = {
        "benchmark_contract": "line-model-candidate-reply-preview-v1",
        "scenario": scenario,
        "question_sha256": sample.get("question_sha256"),
        "request_id": sample.get("request_id"),
        "queue_wait_ms": sample.get("queue_wait_ms"),
        "execution_ms": sample.get("execution_ms"),
        "benchmark_end_to_end_ms": sample.get("benchmark_end_to_end_ms"),
        "validator_result": result.get("validator_result"),
        "validator_reason_codes": list(result.get("validator_reason_codes") or []),
        "candidate_can_replace_reply": False,
        "candidate_reply_sent_to_line": False,
    }
    if result.get("validator_result") != "pass":
        response["preview_rendered"] = False
        return response
    preview = execution.get("preview")
    if not isinstance(preview, dict):
        response.update(
            {
                "preview_rendered": False,
                "preview_rejection_reason": result.get("preview_rejection_reason"),
            }
        )
        return response
    response.update({"preview_rendered": True, "preview": preview})
    return response


def run_live_phase_b_audit_sample(*, scenario: str, code: str = "2330") -> dict[str, Any]:
    """Return one fixed, authenticated canonical packet audit for human review."""

    execution = _execute_live_canonical_shadow_sample(scenario=scenario, code=code)
    sample = execution["response"]
    summary = sample.get("result") if isinstance(sample.get("result"), dict) else {}
    candidate = execution.get("candidate") if isinstance(execution.get("candidate"), dict) else {}
    packet = candidate.get("compacted_packet")
    if not isinstance(packet, dict):
        raise LineModelBenchmarkError(
            "compacted canonical packet is unavailable",
            reason_code="canonical_compacted_packet_unavailable",
        )
    included_sections = [
        key
        for key in (
            "artifact_identity",
            "request",
            "entities",
            "facts",
            "events",
            "technical",
            "referee",
            "conversation",
            "image_observations",
            "omissions",
            "conflicts",
            "render_contract",
        )
        if key in packet and packet.get(key) not in (None, [], {})
    ]
    omitted_sections: list[dict[str, Any]] = []
    if candidate.get("removed_fact_ids"):
        omitted_sections.append(
            {
                "section": "facts",
                "reason": "token_budget_compaction",
                "evidence_ids": list(candidate.get("removed_fact_ids") or []),
            }
        )
    if candidate.get("removed_event_ids"):
        omitted_sections.append(
            {
                "section": "events",
                "reason": "token_budget_compaction",
                "evidence_ids": list(candidate.get("removed_event_ids") or []),
            }
        )
    for omission in packet.get("omissions") or []:
        if isinstance(omission, dict):
            omitted_sections.append(
                {
                    "section": str(omission.get("scope") or "unknown"),
                    "reason": str(omission.get("reason") or "unspecified"),
                }
            )
    preview = execution.get("preview") if isinstance(execution.get("preview"), dict) else None
    canonical_hash = str(summary.get("canonical_answer_text_hash") or "")
    rendered_hash = str((preview or {}).get("sha256") or "")
    return {
        "benchmark_contract": "line-model-canonical-phase-b-audit-v1",
        "scenario": scenario,
        "request_id": sample.get("request_id"),
        "result": summary,
        "analysis_id": summary.get("analysis_id"),
        "snapshot_id": summary.get("snapshot_id"),
        "profile": summary.get("profile"),
        "packet_statistics": {
            "packet_token_count": candidate.get("packet_token_count"),
            "estimated_prompt_token_count": candidate.get("estimated_prompt_token_count"),
            "prompt_token_count": candidate.get("prompt_token_count"),
            "completion_token_count": candidate.get("completion_token_count"),
            "removed_fact_count": candidate.get("removed_fact_count"),
            "removed_event_count": candidate.get("removed_event_count"),
        },
        "compacted_model_fact_packet_v2": packet,
        "included_sections": included_sections,
        "omitted_sections": omitted_sections,
        "actual_model_explanation_blocks": list(candidate.get("explanation_blocks") or []),
        "validator_result": candidate.get("validator_result"),
        "validator_reason_codes": list(candidate.get("validator_reason_codes") or []),
        "rendered_answer": (preview or {}).get("text"),
        "rendered_answer_sha256": rendered_hash or None,
        "canonical_answer_text_hash": canonical_hash or None,
        "web_line_canonical_text_hash_match": bool(
            canonical_hash and rendered_hash and canonical_hash == rendered_hash
        ),
        "candidate_reply_sent_to_line": False,
        "candidate_can_replace_reply": False,
        "raw_model_text_returned": False,
    }


def run_live_stable_reply_sample(*, scenario: str, code: str = "2330") -> dict[str, Any]:
    """Execute the fixed production stock-answer path without sending a LINE message."""

    question = BENCHMARK_QUESTIONS.get(str(scenario))
    if not question:
        raise LineModelBenchmarkError("unknown benchmark scenario", reason_code="unknown_scenario")
    if str(code).strip() != "2330":
        raise LineModelBenchmarkError(
            "stable reply benchmark is fixed to stock code 2330",
            reason_code="invalid_code",
        )
    total_budget_seconds = env_int(
        "LINE_TOTAL_REPLY_BUDGET_SECONDS",
        45,
        minimum=20,
        maximum=50,
    )
    started = time.monotonic()
    admission_before = model_admission_snapshot()
    result = _answer_stock_question_result(
        question,
        deadline_monotonic=started + total_budget_seconds,
        conversation_context=None,
    )
    finished = time.monotonic()
    reply_bytes = result.text.encode("utf-8")
    elapsed_ms = int((finished - started) * 1000)
    return {
        "benchmark_contract": "line-model-live-stable-reply-v1",
        "scenario": scenario,
        "code": "2330",
        "answer_path": result.answer_path,
        "model_output_sha256": getattr(result, "model_output_sha256", None),
        "model_output_characters": getattr(result, "model_output_characters", None),
        "policy_rejection_reason": getattr(result, "policy_rejection_reason", None),
        "reply_utf8_bytes": len(reply_bytes),
        "reply_sha256": hashlib.sha256(reply_bytes).hexdigest(),
        "reply_has_required_disclaimer": result.text.endswith(
            "僅供資料整理，不構成投資建議。"
        ),
        "shadow_request_prepared": result.shadow_request is not None,
        "end_to_end_ms": elapsed_ms,
        "internal_reply_budget_ms": total_budget_seconds * 1000,
        "completed_within_internal_reply_budget": elapsed_ms < total_budget_seconds * 1000,
        "candidate_reply_submitted": False,
        "admission_before": admission_before,
        "admission_after": model_admission_snapshot(),
    }


def schedule_live_background_warmup() -> dict[str, Any]:
    """Queue startup text residency without making the startup request wait."""

    result = ensure_background_text_model_warmup()
    return {
        "benchmark_contract": "line-model-background-warmup-v1",
        **result,
        "warmup_state": background_text_model_warmup_status(),
        "admission_after": model_admission_snapshot(),
    }


def run_live_interactive_warmup() -> dict[str, Any]:
    """Explicitly load the text model through the live interactive controller."""

    resident_before = qwen_model_resident()
    submitted_at = time.monotonic()
    execution = run_interactive_model(
        lambda: qwen_chat(
            "你是本機模型載入驗收探針，只回覆 READY，不輸出其他內容。",
            "READY",
            timeout_seconds=120,
            allow_background_timeout=True,
            max_output_tokens=128,
        ),
        category="interactive_benchmark_warmup",
        deadline_monotonic=time.monotonic() + 125,
    )
    return {
        "benchmark_contract": "line-model-live-interactive-warmup-v1",
        "resident_before": resident_before,
        "resident_after": qwen_model_resident(),
        "queue_wait_ms": execution.queue_wait_ms,
        "execution_ms": execution.execution_ms,
        "total_ms": int((time.monotonic() - submitted_at) * 1000),
        "result_sha256": hashlib.sha256(str(execution.value).encode("utf-8")).hexdigest(),
        "admission_after": model_admission_snapshot(),
    }


def run_live_preemption_probe(*, scenario: str, code: str = "2330") -> dict[str, Any]:
    """Measure a fixed interactive request arriving during an active shadow stream."""

    _require_resident_model()
    job, _question = _benchmark_job(scenario=scenario, code=code)
    future = submit_line_model_shadow(
        job,
        stable_reply_sha256=hashlib.sha256(b"benchmark-preemption").hexdigest(),
    )
    if future is None:
        raise LineModelBenchmarkError(
            "shadow preemption probe was not admitted",
            reason_code="shadow_not_admitted",
        )
    active_deadline = time.monotonic() + 10
    while time.monotonic() < active_deadline:
        snapshot = model_admission_snapshot()
        if snapshot.get("active_category") == "shadow_candidate":
            break
        if future.done():
            raise LineModelBenchmarkError(
                "shadow completed before preemption probe",
                reason_code="shadow_completed_before_probe",
            )
        time.sleep(0.01)
    else:
        raise LineModelBenchmarkError(
            "shadow did not start before probe deadline",
            reason_code="shadow_start_timeout",
        )

    submitted_at = time.monotonic()
    interactive = run_interactive_model(
        lambda: qwen_chat(
            "你是本機排程探針，只回覆 READY，不輸出其他內容。",
            "READY",
            timeout_seconds=30,
            max_output_tokens=128,
        ),
        category="interactive_benchmark_probe",
        deadline_monotonic=time.monotonic() + 35,
    )
    interactive_total_wait_ms = int((time.monotonic() - submitted_at) * 1000)
    shadow_outcome = "unknown"
    shadow_reason = ""
    try:
        future.result(timeout=10)
        shadow_outcome = "completed_before_cancellation"
    except ModelAdmissionError as exc:
        shadow_outcome = (
            "preempted" if exc.reason_code == "shadow_preempted_by_interactive" else "rejected"
        )
        shadow_reason = exc.reason_code
    except FutureTimeoutError:
        shadow_outcome = "still_running"
        shadow_reason = "shadow_preemption_timeout"
    return {
        "benchmark_contract": "line-model-live-preemption-v1",
        "scenario": scenario,
        "interactive_queue_wait_ms": interactive.queue_wait_ms,
        "interactive_execution_ms": interactive.execution_ms,
        "interactive_total_wait_ms": interactive_total_wait_ms,
        "interactive_result_sha256": hashlib.sha256(
            str(interactive.value).encode("utf-8")
        ).hexdigest(),
        "shadow_outcome": shadow_outcome,
        "shadow_reason": shadow_reason,
        "admission_after": model_admission_snapshot(),
    }


def _wait_active(category: str, future: Any, *, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if model_admission_snapshot().get("active_category") == category:
            return
        if future.done():
            raise LineModelBenchmarkError(
                f"{category} completed before contention probe",
                reason_code="contention_completed_before_probe",
            )
        time.sleep(0.01)
    raise LineModelBenchmarkError(
        f"{category} did not start before probe deadline",
        reason_code="contention_start_timeout",
    )


def run_live_memory_contention_probe() -> dict[str, Any]:
    """Measure interactive text arriving during cancellable memory compaction work."""

    _require_resident_model()
    memory_model = env_text("QWEN_MEMORY_MODEL_ID", env_text("QWEN_MODEL_ID", "taiwan-stock-qwen"))
    if not qwen_model_resident(memory_model):
        try:
            run_maintenance_model(
                lambda: qwen_chat(
                    "你是記憶摘要模型，只回覆 READY。",
                    "READY",
                    allow_background_timeout=True,
                    timeout_seconds=120,
                    max_output_tokens=128,
                    model_id=memory_model,
                ),
                category="maintenance_benchmark_memory_warmup",
                cancellable_callable=lambda cancellation_event: qwen_chat(
                    "你是記憶摘要模型，只回覆 READY。",
                    "READY",
                    allow_background_timeout=True,
                    timeout_seconds=120,
                    max_output_tokens=128,
                    model_id=memory_model,
                    cancellation_event=cancellation_event,
                ),
            )
        except (QwenClientError, ModelAdmissionError) as exc:
            raise LineModelBenchmarkError(
                "memory model warmup did not complete",
                reason_code="memory_model_warmup_failed",
            ) from exc
    maintenance = submit_maintenance_model(
        lambda: "unused",
        category="maintenance_conversation_compaction",
        cancellable_callable=lambda cancellation_event: qwen_chat(
            "你只負責壓縮對話，輸出一句不超過一百字的摘要。",
            "使用者持續詢問台積電的基本面、籌碼、技術面、風險與資料限制。請只做摘要。",
            allow_background_timeout=True,
            timeout_seconds=120,
            max_output_tokens=320,
            model_id=memory_model,
            cancellation_event=cancellation_event,
        ),
    )
    _wait_active("maintenance_conversation_compaction", maintenance)
    submitted_at = time.monotonic()
    interactive = run_interactive_model(
        lambda: qwen_chat(
            "你是本機排程探針，只回覆 READY。",
            "READY",
            timeout_seconds=40,
            max_output_tokens=128,
        ),
        category="interactive_benchmark_during_memory_compaction",
        deadline_monotonic=time.monotonic() + 45,
    )
    total_wait_ms = int((time.monotonic() - submitted_at) * 1000)
    maintenance_outcome = "unknown"
    maintenance_reason = ""
    try:
        maintenance.result(timeout=15)
        maintenance_outcome = "completed_before_cancellation"
    except ModelAdmissionError as exc:
        maintenance_outcome = (
            "preempted"
            if exc.reason_code == "maintenance_preempted_by_interactive"
            else "rejected"
        )
        maintenance_reason = exc.reason_code
    except FutureTimeoutError:
        maintenance_outcome = "still_running"
        maintenance_reason = "maintenance_preemption_timeout"
    return {
        "benchmark_contract": "line-model-live-memory-contention-v1",
        "memory_model_id": memory_model,
        "memory_model_resident": qwen_model_resident(memory_model),
        "interactive_queue_wait_ms": interactive.queue_wait_ms,
        "interactive_execution_ms": interactive.execution_ms,
        "interactive_total_wait_ms": total_wait_ms,
        "interactive_result_sha256": hashlib.sha256(
            str(interactive.value).encode("utf-8")
        ).hexdigest(),
        "maintenance_outcome": maintenance_outcome,
        "maintenance_reason": maintenance_reason,
        "admission_after": model_admission_snapshot(),
    }


def _ensure_vision_model_resident() -> str:
    vision_model = env_text("QWEN_VISION_MODEL_ID", "qwen3-vl:8b-instruct")
    if not qwen_model_resident(vision_model):
        try:
            run_maintenance_model(
                lambda: qwen_chat(
                    "你是模型載入探針，只回覆 READY。",
                    "READY",
                    allow_background_timeout=True,
                    timeout_seconds=120,
                    max_output_tokens=128,
                    model_id=vision_model,
                ),
                category="maintenance_benchmark_vision_warmup",
                cancellable_callable=lambda cancellation_event: qwen_chat(
                    "你是模型載入探針，只回覆 READY。",
                    "READY",
                    allow_background_timeout=True,
                    timeout_seconds=120,
                    max_output_tokens=128,
                    model_id=vision_model,
                    cancellation_event=cancellation_event,
                ),
            )
        except (QwenClientError, ModelAdmissionError) as exc:
            raise LineModelBenchmarkError(
                "vision model warmup did not complete",
                reason_code="vision_model_warmup_failed",
            ) from exc
    return vision_model


def _start_vision_probe() -> tuple[str, ThreadPoolExecutor, Any]:
    _require_resident_model()
    vision_model = _ensure_vision_model_resident()
    executor = ThreadPoolExecutor(max_workers=1)
    vision = executor.submit(
        run_interactive_model,
        lambda: qwen_vision_json(
            "只描述可見內容；看不清楚就留空。",
            "這是固定的內部 GPU contention 探針。",
            _VISION_PROBE_PNG,
            timeout_seconds=50,
        ),
        category="interactive_benchmark_vision",
        deadline_monotonic=time.monotonic() + 55,
    )
    _wait_active("interactive_benchmark_vision", vision)
    return vision_model, executor, vision


def _vision_outcome(vision: Any) -> tuple[str, str]:
    try:
        vision.result(timeout=5)
        return "completed", ""
    except FutureTimeoutError:
        return "still_running", "vision_completion_pending"
    except (QwenClientError, ModelAdmissionError, ValueError) as exc:
        return "failed", type(exc).__name__


def run_live_vision_contention_probe() -> dict[str, Any]:
    """Measure text queueing while the real vision path owns the single GPU slot."""

    vision_model, executor, vision = _start_vision_probe()
    try:
        submitted_at = time.monotonic()
        interactive = run_interactive_model(
            lambda: qwen_chat(
                "你是本機排程探針，只回覆 READY。",
                "READY",
                timeout_seconds=50,
                max_output_tokens=128,
            ),
            category="interactive_benchmark_during_vision",
            deadline_monotonic=time.monotonic() + 55,
        )
        total_wait_ms = int((time.monotonic() - submitted_at) * 1000)
        vision_outcome, vision_error = _vision_outcome(vision)
    finally:
        executor.shutdown(wait=True)
    return {
        "benchmark_contract": "line-model-live-vision-contention-v1",
        "vision_model_id": vision_model,
        "vision_model_resident": qwen_model_resident(vision_model),
        "text_model_resident": qwen_model_resident(),
        "interactive_queue_wait_ms": interactive.queue_wait_ms,
        "interactive_execution_ms": interactive.execution_ms,
        "interactive_total_wait_ms": total_wait_ms,
        "interactive_result_sha256": hashlib.sha256(
            str(interactive.value).encode("utf-8")
        ).hexdigest(),
        "vision_outcome": vision_outcome,
        "vision_error_class": vision_error,
        "admission_after": model_admission_snapshot(),
    }


def run_live_vision_stable_reply_probe() -> dict[str, Any]:
    """Run the actual stable reply path while vision owns the shared GPU slot."""

    vision_model, executor, vision = _start_vision_probe()
    try:
        stable = run_live_stable_reply_sample(
            scenario="fundamental_chip_technical_news",
            code="2330",
        )
        vision_outcome_at_stable_reply, vision_error_at_stable_reply = _vision_outcome(vision)
    finally:
        executor.shutdown(wait=True)
    vision_outcome, vision_error = _vision_outcome(vision)
    return {
        "benchmark_contract": "line-model-live-vision-stable-reply-v1",
        "vision_model_id": vision_model,
        "vision_model_resident": qwen_model_resident(vision_model),
        "text_model_resident": qwen_model_resident(),
        "stable_reply": stable,
        "vision_outcome_at_stable_reply": vision_outcome_at_stable_reply,
        "vision_error_at_stable_reply": vision_error_at_stable_reply,
        "vision_outcome": vision_outcome,
        "vision_error_class": vision_error,
        "admission_after": model_admission_snapshot(),
    }


def run_live_cold_load_probe(*, maintenance_window_confirmed: bool = False) -> dict[str, Any]:
    """Run an explicitly enabled maintenance lifecycle, never a live reply sample."""

    if not env_bool("LINE_MODEL_COLD_PROBE_ENABLED", False):
        raise LineModelBenchmarkError("cold probe is disabled", reason_code="cold_probe_disabled")
    if maintenance_window_confirmed is not True:
        raise LineModelBenchmarkError(
            "confirmed maintenance window is required", reason_code="maintenance_window_required"
        )
    load_timeout = env_int("LINE_MODEL_COLD_PROBE_TIMEOUT_SECONDS", 1260, minimum=30, maximum=3600)

    def lifecycle() -> dict[str, Any]:
        result: dict[str, Any] = {"resident_before": qwen_model_resident(), "unloaded": False}
        try:
            started = time.monotonic()
            result["unloaded"] = qwen_unload_text_model()
            result["unload_ms"] = int((time.monotonic() - started) * 1000)
            if not result["unloaded"]:
                raise QwenClientError("model remained resident", reason_code="model_unload_failed")
            started = time.monotonic()
            result["native_load_metrics"] = qwen_preload_text_model(timeout_seconds=load_timeout)
            result["preload_wall_ms"] = int((time.monotonic() - started) * 1000)
            if not qwen_model_resident():
                raise QwenClientError("model not resident after load", reason_code="model_not_resident_after_load")
            started = time.monotonic()
            answer = qwen_chat(
                "你是本機載入後驗收探針，只回覆 READY。", "READY",
                timeout_seconds=40, max_output_tokens=128,
            )
            result["post_load_inference_ms"] = int((time.monotonic() - started) * 1000)
            result["post_load_result_sha256"] = hashlib.sha256(answer.encode("utf-8")).hexdigest()
            result["probe_status"] = "pass"
        except QwenClientError as exc:
            # Do not retry a possibly still-loading runner or silently lose the failure.
            result["probe_status"] = "fail"
            result["failure_reason_code"] = exc.reason_code
        finally:
            result["resident_after"] = qwen_model_resident()
            result["recovery_required"] = not result["resident_after"]
        if result["recovery_required"] and result.get("probe_status") == "pass":
            result.update(probe_status="fail", failure_reason_code="model_not_resident_after_probe")
        return result

    started = time.monotonic()
    execution = run_maintenance_model(
        lifecycle,
        category="maintenance_benchmark_cold_lifecycle",
        predicted_duration_ms=(load_timeout + 70) * 1000,
    )
    return {
        "benchmark_contract": "line-model-maintenance-cold-load-v2",
        "evidence_kind": "maintenance_cold_lifecycle",
        "valid_for_live_reply_deadline": False,
        "valid_for_load_matrix": False,
        "load_timeout_seconds": load_timeout,
        "maintenance_queue_wait_ms": execution.queue_wait_ms,
        "maintenance_execution_ms": execution.execution_ms,
        "maintenance_total_ms": int((time.monotonic() - started) * 1000),
        **execution.value,
        "admission_after": model_admission_snapshot(),
    }
