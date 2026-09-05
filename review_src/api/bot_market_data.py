from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth.bot_dependencies import require_bot_market_data_token
from services.bot_market_data_service import (
    bot_market_data_readiness,
    build_bot_daily_history,
    build_canonical_close_batch_snapshot,
    build_bot_intraday_trade_page,
    build_bot_market_brief,
    build_bot_stock_screen,
    resolve_bot_stock_query,
)
from services.canonical_analysis_orchestrator import run_canonical_analysis
from services.canonical_question_analysis_service import run_canonical_question_analysis
from services.canonical_model_packet_orchestrator import (
    build_canonical_question_model_packet,
)
from services.canonical_model_answer_service import (
    CanonicalModelAnswerFinalizationError,
    finalize_authorized_canonical_model_answer,
)


router = APIRouter(
    prefix="/api/bot/market-data",
    dependencies=[Depends(require_bot_market_data_token)],
    tags=["bot-market-data"],
)


@router.get("/readyz")
def api_bot_market_data_readiness() -> dict[str, Any]:
    result = bot_market_data_readiness()
    if not result.get("ready"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=result,
        )
    return result


@router.post("/analysis")
def api_bot_canonical_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    """LINE transport adapter for the same persisted Web analysis artifact."""

    try:
        if str(payload.get("query") or "").strip():
            return run_canonical_question_analysis(
                query=str(payload.get("query") or ""),
                delivery_channel="line",
                conversation_context=(
                    payload.get("conversation_context")
                    if isinstance(payload.get("conversation_context"), dict)
                    else {}
                ),
                trade_date=str(payload.get("trade_date") or "") or None,
                analysis_cutoff=str(payload.get("analysis_cutoff") or "") or None,
                request_received_at=str(payload.get("request_received_at") or "") or None,
                profile=str(payload.get("profile") or "focused"),
            )
        return run_canonical_analysis(
            code=str(payload.get("code") or ""),
            delivery_channel="line",
            trade_date=str(payload.get("trade_date") or "") or None,
            analysis_cutoff=str(payload.get("analysis_cutoff") or "") or None,
            request_received_at=str(payload.get("request_received_at") or "") or None,
            conversation_context_digest=(
                str(payload.get("conversation_context_digest") or "") or None
            ),
            profile=str(payload.get("profile") or "focused"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/analysis/model-packet")
def api_bot_canonical_model_packet(payload: dict[str, Any]) -> dict[str, Any]:
    """Authenticated LINE transport for the sealed canonical model packet."""

    try:
        return build_canonical_question_model_packet(
            query=str(payload.get("query") or ""),
            conversation_context=(
                payload.get("conversation_context")
                if isinstance(payload.get("conversation_context"), dict)
                else {}
            ),
            requested_scopes=(
                payload.get("requested_scopes")
                if isinstance(payload.get("requested_scopes"), list)
                else []
            ),
            profile=str(payload.get("profile") or "focused"),
            trade_date=str(payload.get("trade_date") or "") or None,
            analysis_cutoff=str(payload.get("analysis_cutoff") or "") or None,
            request_received_at=str(payload.get("request_received_at") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/analysis/model-answer")
def api_bot_finalize_canonical_model_answer(payload: dict[str, Any]) -> dict[str, Any]:
    """Authenticated write boundary for one independently revalidated model answer."""

    try:
        return finalize_authorized_canonical_model_answer(
            payload.get("submission") if isinstance(payload.get("submission"), dict) else {},
            cohort_key=str(payload.get("cohort_key") or ""),
        )
    except CanonicalModelAnswerFinalizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"reason_code": exc.reason_code},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/resolve")
def api_bot_resolve_stock(
    q: str = Query(min_length=1, max_length=200),
) -> dict[str, Any]:
    return resolve_bot_stock_query(q)


@router.get("/screen")
def api_bot_stock_screen(
    strategy: str = Query(default="balanced", max_length=32),
    limit: int = Query(default=5, ge=1, le=8),
) -> dict[str, Any]:
    return build_bot_stock_screen(strategy=strategy, limit=limit)


@router.get("/market-brief")
def api_bot_market_brief(
    q: str = Query(default="", max_length=200),
) -> dict[str, Any]:
    return build_bot_market_brief(q)


@router.get("/{code}/daily")
def api_bot_daily_market_data(
    code: str,
    trade_date: str | None = None,
    include_levels: bool = True,
    level_limit: int = Query(default=100, ge=1, le=200),
    analysis_mode: str = Query(
        default="close_batch",
        pattern="^(close_batch|intraday)$",
    ),
) -> dict[str, Any]:
    return build_canonical_close_batch_snapshot(
        code,
        trade_date=trade_date,
        include_levels=include_levels,
        level_limit=level_limit,
        analysis_mode=analysis_mode,
        allow_live_quote_fetch=analysis_mode == "intraday" and trade_date is None,
    )


@router.get("/{code}/history")
def api_bot_daily_history(
    code: str,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=1_000_000),
) -> dict[str, Any]:
    return build_bot_daily_history(
        code,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.get("/{code}/trades")
def api_bot_intraday_trades(
    code: str,
    trade_date: str,
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1_000_000),
) -> dict[str, Any]:
    if str(os.getenv("BOT_ENABLE_UNVERIFIED_TRADES", "false")).strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Raw trade API is disabled until full-day pagination completeness is persisted",
        )
    return build_bot_intraday_trade_page(
        code,
        trade_date=trade_date,
        limit=limit,
        offset=offset,
    )
