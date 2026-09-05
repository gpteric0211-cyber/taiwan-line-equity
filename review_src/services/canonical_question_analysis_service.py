from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from services.canonical_analysis_orchestrator import run_canonical_analysis
from services.conversation_projection_v1 import build_conversation_projection_v1
from services.stock_entity_registry_service import resolve_stock_entity_query_from_repository


def run_canonical_question_analysis(
    *,
    query: str,
    delivery_channel: str,
    conversation_context: Mapping[str, Any] | None = None,
    trade_date: str | None = None,
    analysis_cutoff: str | None = None,
    request_received_at: str | None = None,
    profile: str = "focused",
    resolver: Callable[..., dict[str, Any]] = resolve_stock_entity_query_from_repository,
    orchestrator: Callable[..., dict[str, Any]] = run_canonical_analysis,
) -> dict[str, Any]:
    """Resolve identity and bounded context before invoking the one artifact use case."""

    context = dict(conversation_context or {})
    active_code = str(
        ((context.get("active_stock") or {}).get("code") if isinstance(context.get("active_stock"), Mapping) else "")
        or context.get("code")
        or ""
    )
    classification_started = time.perf_counter_ns()
    resolution = resolver(query, active_stock_code=active_code or None)
    classification_ms = round(
        (time.perf_counter_ns() - classification_started) / 1_000_000,
        3,
    )
    if not resolution.get("ok"):
        return {
            "ok": False,
            "status": "clarification_required",
            "requires_clarification": True,
            "resolution": resolution,
            "artifact_created": False,
            "stage_timings_ms": {
                "classification": classification_ms,
                "projection": 0.0,
                "retrieval": 0.0,
            },
        }
    entities = list(resolution.get("entities") or [])
    comparisons = list(resolution.get("comparison_stocks") or [])
    projection_started = time.perf_counter_ns()
    projection = build_conversation_projection_v1(
        context,
        recent_exchanges=list(context.get("recent_exchanges") or []),
        rolling_summary=(
            context.get("rolling_summary")
            if isinstance(context.get("rolling_summary"), Mapping)
            else {"summary": context.get("rolling_summary")}
        ),
        resolved_entities=entities,
        comparison_entities=comparisons,
        last_analysis_id=str(context.get("last_analysis_id") or "") or None,
        last_analysis_cutoff=str(context.get("last_analysis_cutoff") or "") or None,
    )
    projection_ms = round(
        (time.perf_counter_ns() - projection_started) / 1_000_000,
        3,
    )
    primary = resolution.get("stock") or entities[0]
    retrieval_started = time.perf_counter_ns()
    result = orchestrator(
        code=str(primary.get("code") or ""),
        delivery_channel=delivery_channel,
        trade_date=trade_date,
        analysis_cutoff=analysis_cutoff,
        request_received_at=request_received_at,
        conversation_context_digest=projection["projection_digest"],
        profile=profile,
        entity_resolution=resolution,
        conversation_projection=projection,
    )
    retrieval_ms = round(
        (time.perf_counter_ns() - retrieval_started) / 1_000_000,
        3,
    )
    result["requires_clarification"] = False
    result["resolution"] = resolution
    result["stage_timings_ms"] = {
        "classification": classification_ms,
        "projection": projection_ms,
        "retrieval": retrieval_ms,
    }
    return result
