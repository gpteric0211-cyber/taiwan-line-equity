from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import requests

from core.line_bot_config import env_int, env_text


class BotMarketDataClientError(RuntimeError):
    pass


def _base_url() -> str:
    value = env_text("BOT_MARKET_DATA_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BotMarketDataClientError("BOT_MARKET_DATA_BASE_URL is invalid")
    return value


def _headers() -> dict[str, str]:
    token = env_text("BOT_MARKET_DATA_TOKEN")
    if len(token) < 32:
        raise BotMarketDataClientError("BOT_MARKET_DATA_TOKEN is missing or too short")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _get(path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    timeout = env_int("BOT_MARKET_DATA_TIMEOUT_SECONDS", 15, minimum=3, maximum=45)
    try:
        response = requests.get(
            f"{_base_url()}{path}",
            params=params or {},
            headers=_headers(),
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise BotMarketDataClientError("read-only market-data API is unavailable") from exc
    if not isinstance(payload, dict):
        raise BotMarketDataClientError("read-only market-data API returned an invalid payload")
    return payload


def _post(path: str, *, payload: dict[str, Any]) -> dict[str, Any]:
    timeout = env_int("BOT_MARKET_DATA_TIMEOUT_SECONDS", 15, minimum=3, maximum=45)
    try:
        response = requests.post(
            f"{_base_url()}{path}",
            json=payload,
            headers={**_headers(), "Content-Type": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise BotMarketDataClientError("canonical analysis API is unavailable") from exc
    if not isinstance(result, dict):
        raise BotMarketDataClientError("canonical analysis API returned an invalid payload")
    return result


def resolve_stock_query(query: str) -> dict[str, Any]:
    return _get("/api/bot/market-data/resolve", params={"q": query})


def fetch_stock_screen(*, strategy: str = "balanced", limit: int = 5) -> dict[str, Any]:
    return _get(
        "/api/bot/market-data/screen",
        params={"strategy": strategy, "limit": max(1, min(int(limit), 8))},
    )


def fetch_market_brief(query: str) -> dict[str, Any]:
    return _get("/api/bot/market-data/market-brief", params={"q": str(query or "")[:200]})


def fetch_daily_market_data(
    code: str,
    *,
    trade_date: str | None = None,
    analysis_mode: str = "close_batch",
) -> dict[str, Any]:
    normalized_mode = str(analysis_mode or "").strip().lower()
    if normalized_mode not in {"close_batch", "intraday"}:
        raise BotMarketDataClientError("analysis mode is invalid")
    params: dict[str, Any] = {
        "include_levels": True,
        "level_limit": 60,
        "analysis_mode": normalized_mode,
    }
    if trade_date:
        params["trade_date"] = trade_date
    return _get(f"/api/bot/market-data/{code}/daily", params=params)


def fetch_canonical_analysis(
    code: str,
    *,
    analysis_cutoff: str | None = None,
    trade_date: str | None = None,
    conversation_context_digest: str | None = None,
    profile: str = "focused",
) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "profile": profile}
    if analysis_cutoff:
        payload["analysis_cutoff"] = analysis_cutoff
    if trade_date:
        payload["trade_date"] = trade_date
    if conversation_context_digest:
        payload["conversation_context_digest"] = conversation_context_digest
    return _post("/api/bot/market-data/analysis", payload=payload)


def fetch_canonical_question_analysis(
    query: str,
    *,
    conversation_context: dict[str, Any] | None = None,
    analysis_cutoff: str | None = None,
    trade_date: str | None = None,
    profile: str = "focused",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": str(query or "")[:200],
        "conversation_context": dict(conversation_context or {}),
        "profile": profile,
    }
    if analysis_cutoff:
        payload["analysis_cutoff"] = analysis_cutoff
    if trade_date:
        payload["trade_date"] = trade_date
    return _post("/api/bot/market-data/analysis", payload=payload)


def fetch_canonical_question_model_packet(
    query: str,
    *,
    requested_scopes: list[str],
    conversation_context: dict[str, Any] | None = None,
    analysis_cutoff: str | None = None,
    trade_date: str | None = None,
    request_received_at: str | None = None,
    profile: str = "focused",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": str(query or "")[:200],
        "requested_scopes": list(requested_scopes),
        "conversation_context": dict(conversation_context or {}),
        "profile": profile,
    }
    if analysis_cutoff:
        payload["analysis_cutoff"] = analysis_cutoff
    if trade_date:
        payload["trade_date"] = trade_date
    if request_received_at:
        payload["request_received_at"] = request_received_at
    return _post("/api/bot/market-data/analysis/model-packet", payload=payload)


def finalize_canonical_model_answer(
    candidate_result: dict[str, Any],
    *,
    cohort_key: str,
) -> dict[str, Any]:
    """Submit only the bounded packet, validated output, and release metadata."""

    allowed = (
        "candidate_version",
        "compacted_packet",
        "validated_model_output",
        "model_output_sha256",
        "model_id",
        "model_digest",
        "generation_schema_version",
        "generation_schema_sha256",
        "generation_prompt_sha256",
        "packet_digest",
        "compacted_packet_sha256",
    )
    submission = {key: candidate_result.get(key) for key in allowed}
    return _post(
        "/api/bot/market-data/analysis/model-answer",
        payload={"cohort_key": str(cohort_key or ""), "submission": submission},
    )


def fetch_daily_history(code: str, *, limit: int = 20) -> dict[str, Any]:
    return _get(
        f"/api/bot/market-data/{code}/history",
        params={"limit": max(1, min(int(limit), 60)), "offset": 0},
    )
