from __future__ import annotations

"""Deterministic offline/shadow execution for the canonical model packet.

This service is intentionally disconnected from every stable Web/LINE reply
path.  It may produce evaluation evidence, but its result is never eligible to
replace the rule-based referee or a user-visible stable answer.
"""

import hashlib
import json
import time
from dataclasses import asdict
from threading import Event
from typing import Any, Mapping

from adapter.qwen_local import QwenClientError, qwen_chat_detailed
from core.line_bot_config import env_int, env_text
from core.line_model_contract import (
    MODEL_FACT_PACKET_VERSION,
    TOKEN_ESTIMATOR_VERSION,
    compact_packet_to_token_budget,
    conservative_prompt_token_estimate,
    select_context_profile,
)
from core.line_model_output_schema import (
    MODEL_OUTPUT_SCHEMA_VERSION,
    model_analysis_output_schema,
)
from core.line_model_validation import (
    deterministic_limitation_placeholder_repair,
    validate_model_analysis_v2,
)
from services.line_model_shadow_service import (
    MODEL_ANALYSIS_FINAL_GUARD,
    MODEL_ANALYSIS_SYSTEM_PROMPT,
    _generation_request_guard,
)
from services.model_admission_service import (
    ModelAdmissionError,
    run_interactive_model,
    run_shadow_model,
)


CANONICAL_CANDIDATE_VERSION = "canonical-model-candidate-v1"
EVALUATION_DECODING_SEED = 20260901
ALLOWED_EXECUTION_MODES = {"offline", "shadow"}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _execute_admitted(
    question: str,
    packet: Mapping[str, Any],
    *,
    execution_mode: str,
    cancellation_event: Event,
    queue_wait_ms: int,
    timeout_seconds: float | None = None,
    allow_background_timeout: bool = True,
) -> dict[str, Any]:
    started = time.monotonic()
    if str(packet.get("contract_version") or "") != MODEL_FACT_PACKET_VERSION:
        raise ValueError("packet contract_version must be model-fact-packet-v2")
    request = packet.get("request") if isinstance(packet.get("request"), Mapping) else {}
    packet_build_started = time.perf_counter_ns()
    depth = "comprehensive" if request.get("depth") == "comprehensive" else "focused"
    scopes = list(dict.fromkeys(str(item) for item in request.get("scopes") or [] if str(item)))
    response_schema = model_analysis_output_schema(depth)
    schema_json = json.dumps(response_schema, ensure_ascii=False, separators=(",", ":"))
    generation_guard = f"{MODEL_ANALYSIS_FINAL_GUARD}{_generation_request_guard(scopes)}"
    system_prompt = f"{MODEL_ANALYSIS_SYSTEM_PROMPT}\nOUTPUT_JSON_SCHEMA：{schema_json}"
    actual_context = env_int("QWEN_CONTEXT_TOKENS", 16_384, minimum=4_096, maximum=262_144)
    reserved_output = env_int("QWEN_MAX_OUTPUT_TOKENS", 900, minimum=128, maximum=8_192)
    selection = select_context_profile(
        depth,
        actual_context,
        requested_profile=env_text("LINE_MODEL_V2_PROFILE") or None,
    )
    compacted = compact_packet_to_token_budget(
        dict(packet),
        system_prompt=system_prompt,
        question=f"{question}\n{generation_guard}",
        profile=selection.profile,
        actual_context=actual_context,
        reserved_output_tokens=reserved_output,
    )
    bounded_packet = compacted.packet
    packet_json = json.dumps(
        bounded_packet,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    user_prompt = (
        f"使用者問題：{question}\nMODEL_FACT_PACKET_V2：{packet_json}\n"
        f"{generation_guard}"
    )
    packet_build_ms = round(
        (time.perf_counter_ns() - packet_build_started) / 1_000_000,
        3,
    )
    model_output = ""
    runtime_model_id = env_text("QWEN_MODEL_ID", "taiwan-stock-qwen")
    validated_output = ""
    finish_reason = "preflight_rejected"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    model_latency_ms = 0
    error_classification: str | None = None
    raw_validator = None
    validator = None
    repair_codes: tuple[str, ...] = ()
    validation_ms = 0.0
    if compacted.compacted_preflight.ready:
        model_started = time.monotonic()
        try:
            configured_timeout = float(
                env_int(
                    "LINE_MODEL_V2_SHADOW_TIMEOUT_SECONDS",
                    90,
                    minimum=10,
                    maximum=120,
                )
            )
            request_timeout = (
                min(configured_timeout, max(5.0, float(timeout_seconds)))
                if timeout_seconds is not None
                else configured_timeout
            )
            completion = qwen_chat_detailed(
                system_prompt,
                user_prompt,
                timeout_seconds=request_timeout,
                allow_background_timeout=allow_background_timeout,
                max_output_tokens=reserved_output,
                cancellation_event=cancellation_event,
                response_schema=response_schema,
                temperature=0.0,
                seed=EVALUATION_DECODING_SEED,
            )
            runtime_model_id = str(completion.model or runtime_model_id)
            model_latency_ms = int((time.monotonic() - model_started) * 1000)
            model_output = completion.text
            finish_reason = completion.finish_reason
            prompt_tokens = completion.prompt_tokens
            completion_tokens = completion.completion_tokens
            validation_started = time.perf_counter_ns()
            raw_validator = validate_model_analysis_v2(model_output, bounded_packet)
            validator = raw_validator
            if not raw_validator.passed:
                repaired, repair_codes = deterministic_limitation_placeholder_repair(
                    model_output,
                    bounded_packet,
                )
                if repaired is not None and repair_codes:
                    repaired_validator = validate_model_analysis_v2(repaired, bounded_packet)
                    if repaired_validator.passed:
                        validator = repaired_validator
                        validated_output = json.dumps(
                            repaired,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
            validation_ms = round(
                (time.perf_counter_ns() - validation_started) / 1_000_000,
                3,
            )
        except QwenClientError as exc:
            model_latency_ms = int((time.monotonic() - model_started) * 1000)
            finish_reason = "model_error"
            error_classification = str(
                getattr(exc, "reason_code", "qwen_request_failed")
            )
            if cancellation_event.is_set():
                raise ModelAdmissionError(
                    "canonical shadow candidate yielded to interactive work",
                    reason_code="shadow_preempted_by_interactive",
                ) from exc

    validator_passed = bool(validator and validator.passed)
    validator_reasons = (
        list(validator.reason_codes)
        if validator is not None
        else [error_classification or compacted.compacted_preflight.reason]
    )
    rendered_output = validated_output or model_output
    return {
        "candidate_version": CANONICAL_CANDIDATE_VERSION,
        "execution_mode": execution_mode,
        "candidate_can_replace_stable": False,
        "candidate_can_override_referee": False,
        "depth": depth,
        "scopes": scopes,
        "decoding": {"temperature": 0.0, "seed": EVALUATION_DECODING_SEED},
        "model_id": runtime_model_id,
        "model_digest": env_text("QWEN_MODEL_DIGEST") or None,
        "profile": selection.profile.name,
        "profile_release_state": selection.profile.release_state,
        "profile_fallback_reason": selection.fallback_reason,
        "token_estimator": TOKEN_ESTIMATOR_VERSION,
        "generation_schema_version": MODEL_OUTPUT_SCHEMA_VERSION,
        "generation_schema_sha256": _sha256(schema_json),
        "generation_prompt_sha256": _sha256(f"{system_prompt}\n{generation_guard}"),
        "packet_digest": str(packet.get("packet_digest") or _sha256(packet_json)),
        "compacted_packet_sha256": _sha256(packet_json),
        "estimated_prompt_token_count": compacted.compacted_preflight.estimated_prompt_tokens,
        "prompt_token_count": prompt_tokens,
        "completion_token_count": completion_tokens,
        "packet_token_count": conservative_prompt_token_estimate(
            packet_json,
            template_overhead=0,
        ),
        "preflight": asdict(compacted.compacted_preflight),
        "removed_fact_count": len(compacted.removed_fact_ids),
        "removed_event_count": len(compacted.removed_event_ids),
        "removed_fact_ids": list(compacted.removed_fact_ids),
        "removed_event_ids": list(compacted.removed_event_ids),
        "queue_wait_ms": max(0, int(queue_wait_ms)),
        "model_latency_ms": model_latency_ms,
        "total_duration_ms": int((time.monotonic() - started) * 1000),
        "stage_timings_ms": {
            "packet_build": packet_build_ms,
            "generation": model_latency_ms,
            "validation": validation_ms,
        },
        "finish_reason": finish_reason,
        "raw_validator_result": (
            "pass" if raw_validator and raw_validator.passed else "reject"
            if raw_validator is not None
            else "not_run"
        ),
        "raw_validator_reason_codes": list(raw_validator.reason_codes) if raw_validator else [],
        "validator_result": "pass" if validator_passed else "reject",
        "validator_reason_codes": validator_reasons,
        "ungrounded_claim_count": int(validator.ungrounded_claim_count if validator else 0),
        "referee_override_count": int(validator.referee_override_count if validator else 0),
        "deterministic_repair_applied": bool(validated_output),
        "deterministic_repair_codes": list(repair_codes),
        "error_classification": error_classification,
        "model_output_sha256": _sha256(rendered_output) if rendered_output else None,
        "model_output": model_output,
        "validated_model_output": rendered_output,
        "explanation_blocks": list((validator.analysis or {}).get("explanation_blocks") or [])
        if validator
        else [],
        "used_event_ids": list((validator.analysis or {}).get("used_event_ids") or [])
        if validator
        else [],
        "research_limitations": list(
            (validator.analysis or {}).get("research_limitations") or []
        )
        if validator
        else [],
        "rendered_blocks": list(validator.rendered_blocks) if validator else [],
        "compacted_packet": bounded_packet,
    }


def run_canonical_model_candidate(
    question: str,
    packet: Mapping[str, Any],
    *,
    execution_mode: str = "offline",
) -> dict[str, Any]:
    """Run one explicit candidate evaluation through shadow GPU admission."""

    mode = str(execution_mode or "").strip().lower()
    if mode not in ALLOWED_EXECUTION_MODES:
        raise ValueError("canonical candidate execution_mode must be offline or shadow")
    queue_wait = {"milliseconds": 0}

    def record_start(milliseconds: int) -> None:
        queue_wait["milliseconds"] = max(0, int(milliseconds))

    execution = run_shadow_model(
        lambda: None,
        on_start=record_start,
        cancellable_callable=lambda cancellation_event: _execute_admitted(
            str(question),
            packet,
            execution_mode=mode,
            cancellation_event=cancellation_event,
            queue_wait_ms=queue_wait["milliseconds"],
        ),
    )
    return execution.value


def run_canonical_model_candidate_interactive(
    question: str,
    packet: Mapping[str, Any],
    *,
    work_stop_deadline_monotonic: float,
) -> dict[str, Any]:
    """Run one canary candidate inside the user-visible work-stop deadline."""

    remaining = float(work_stop_deadline_monotonic) - time.monotonic()
    if remaining < 5:
        raise ModelAdmissionError(
            "canonical candidate has insufficient work-stop budget",
            reason_code="candidate_work_stop_budget_exhausted",
        )
    cancellation_event = Event()
    execution = run_interactive_model(
        lambda: _execute_admitted(
            str(question),
            packet,
            execution_mode="canary",
            cancellation_event=cancellation_event,
            queue_wait_ms=0,
            timeout_seconds=max(5.0, work_stop_deadline_monotonic - time.monotonic()),
            allow_background_timeout=False,
        ),
        category="interactive_stock_analysis",
        deadline_monotonic=work_stop_deadline_monotonic,
    )
    result = dict(execution.value)
    result["queue_wait_ms"] = execution.queue_wait_ms
    result["candidate_can_replace_stable"] = False
    result["release_authorization_required_for_delivery"] = True
    return result
