from __future__ import annotations

"""Authenticated POST-only endpoints for fixed live model benchmark workloads."""

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status

from auth.bot_dependencies import require_bot_market_data_token
from core.line_bot_config import env_bool, env_int
from core.release_source_fingerprint import current_runtime_source_fingerprint
from services.line_model_benchmark_service import (
    BENCHMARK_QUESTIONS,
    LineModelBenchmarkError,
    control_live_research_cache,
    run_live_interactive_warmup,
    run_live_cold_load_probe,
    run_live_candidate_reply_preview,
    run_live_memory_contention_probe,
    run_live_phase_b_audit_sample,
    run_live_preemption_probe,
    run_live_shadow_sample,
    run_live_stable_reply_sample,
    run_live_vision_contention_probe,
    run_live_vision_stable_reply_probe,
    schedule_live_background_warmup,
)


router = APIRouter(
    prefix="/internal/line-model-benchmark",
    dependencies=[Depends(require_bot_market_data_token)],
    tags=["internal-line-model-benchmark"],
)


LOADED_RUNTIME_SOURCE_FINGERPRINT = current_runtime_source_fingerprint()


def _require_enabled() -> None:
    if not env_bool("LINE_MODEL_BENCHMARK_ENABLED", False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="benchmark endpoint is disabled",
        )


@router.get("/source-fingerprint", include_in_schema=False)
def source_fingerprint() -> dict[str, Any]:
    """Return hashes captured when this running API module was imported."""

    _require_enabled()
    return {
        **LOADED_RUNTIME_SOURCE_FINGERPRINT,
        "source_hashes": dict(LOADED_RUNTIME_SOURCE_FINGERPRINT["source_hashes"]),
    }


def _require_background_warmup_enabled() -> None:
    if not env_bool("LINE_MODEL_BACKGROUND_PREWARM", False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="background warmup is disabled",
        )


def _parameters(payload: dict[str, Any]) -> tuple[str, str]:
    scenario = str(payload.get("scenario") or "")
    code = str(payload.get("code") or "2330")
    if scenario not in BENCHMARK_QUESTIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="unknown benchmark scenario",
        )
    return scenario, code


def _translate_error(exc: LineModelBenchmarkError) -> HTTPException:
    code = status.HTTP_409_CONFLICT
    if exc.reason_code in {
        "unknown_scenario",
        "invalid_code",
        "invalid_research_cache_mode",
        "research_not_requested",
    }:
        code = status.HTTP_422_UNPROCESSABLE_ENTITY
    elif exc.reason_code == "benchmark_timeout":
        code = status.HTTP_504_GATEWAY_TIMEOUT
    return HTTPException(
        status_code=code,
        detail={"reason_code": exc.reason_code, "message": str(exc)},
    )


@router.post("/interactive-warmup", include_in_schema=False)
def interactive_warmup(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _require_enabled()
    if payload.get("acknowledge_possible_interactive_delay") is not True:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="explicit maintenance-delay acknowledgement is required",
        )
    return run_live_interactive_warmup()


@router.post("/background-warmup", include_in_schema=False)
def background_warmup(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _require_background_warmup_enabled()
    return schedule_live_background_warmup()


def _require_maintenance_ack(payload: dict[str, Any]) -> None:
    if payload.get("acknowledge_possible_interactive_delay") is not True:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="explicit maintenance-delay acknowledgement is required",
        )


@router.post("/shadow-sample", include_in_schema=False)
def shadow_sample(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _require_enabled()
    scenario, code = _parameters(payload)
    try:
        return run_live_shadow_sample(scenario=scenario, code=code)
    except LineModelBenchmarkError as exc:
        raise _translate_error(exc) from exc


@router.post("/stable-reply-sample", include_in_schema=False)
def stable_reply_sample(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _require_enabled()
    scenario, code = _parameters(payload)
    try:
        return run_live_stable_reply_sample(scenario=scenario, code=code)
    except LineModelBenchmarkError as exc:
        raise _translate_error(exc) from exc


@router.post("/candidate-reply-preview", include_in_schema=False)
def candidate_reply_preview(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _require_enabled()
    scenario, code = _parameters(payload)
    try:
        return run_live_candidate_reply_preview(scenario=scenario, code=code)
    except LineModelBenchmarkError as exc:
        raise _translate_error(exc) from exc


@router.post("/phase-b-audit-sample", include_in_schema=False)
def phase_b_audit_sample(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _require_enabled()
    scenario, code = _parameters(payload)
    try:
        return run_live_phase_b_audit_sample(scenario=scenario, code=code)
    except LineModelBenchmarkError as exc:
        raise _translate_error(exc) from exc


@router.post("/research-cache-control", include_in_schema=False)
def research_cache_control(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _require_enabled()
    scenario, code = _parameters(payload)
    try:
        return control_live_research_cache(
            scenario=scenario,
            code=code,
            mode=str(payload.get("mode") or ""),
        )
    except LineModelBenchmarkError as exc:
        raise _translate_error(exc) from exc


@router.post("/preemption-probe", include_in_schema=False)
def preemption_probe(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _require_enabled()
    scenario, code = _parameters(payload)
    try:
        return run_live_preemption_probe(scenario=scenario, code=code)
    except LineModelBenchmarkError as exc:
        raise _translate_error(exc) from exc


@router.post("/memory-contention-probe", include_in_schema=False)
def memory_contention_probe(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _require_enabled()
    _require_maintenance_ack(payload)
    try:
        return run_live_memory_contention_probe()
    except LineModelBenchmarkError as exc:
        raise _translate_error(exc) from exc


@router.post("/vision-contention-probe", include_in_schema=False)
def vision_contention_probe(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _require_enabled()
    _require_maintenance_ack(payload)
    try:
        return run_live_vision_contention_probe()
    except LineModelBenchmarkError as exc:
        raise _translate_error(exc) from exc


@router.post("/vision-stable-reply-probe", include_in_schema=False)
def vision_stable_reply_probe(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _require_enabled()
    _require_maintenance_ack(payload)
    try:
        return run_live_vision_stable_reply_probe()
    except LineModelBenchmarkError as exc:
        raise _translate_error(exc) from exc


@router.get("/cold-load-capability", include_in_schema=False)
def cold_load_capability() -> dict[str, Any]:
    """Read-only handshake: a new collector must not unload through an old server."""

    _require_enabled()
    return {
        "benchmark_contract": "line-model-maintenance-cold-load-v2",
        "enabled": env_bool("LINE_MODEL_COLD_PROBE_ENABLED", False),
        "maintenance_only": True,
        "exclusive_admission": True,
        "load_timeout_seconds": env_int(
            "LINE_MODEL_COLD_PROBE_TIMEOUT_SECONDS", 1260, minimum=30, maximum=3600
        ),
    }


@router.post("/cold-load-probe", include_in_schema=False)
def cold_load_probe(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _require_enabled()
    _require_maintenance_ack(payload)
    try:
        return run_live_cold_load_probe(
            maintenance_window_confirmed=payload.get("maintenance_window_confirmed") is True
        )
    except LineModelBenchmarkError as exc:
        raise _translate_error(exc) from exc
