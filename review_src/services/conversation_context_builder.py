from __future__ import annotations

from typing import Any


def _clean_text(value: Any, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _summary_text(value: dict[str, Any] | None, maximum: int) -> str:
    if not isinstance(value, dict):
        return ""
    return _clean_text(value.get("summary"), maximum)


def build_conversation_context(
    session: dict[str, Any] | None,
    *,
    recent_exchanges: list[dict[str, Any]] | None = None,
    rolling_summary: dict[str, Any] | None = None,
    stock_summary: dict[str, Any] | None = None,
    character_budget: int = 6000,
) -> dict[str, Any]:
    """Build bounded history for Qwen; current market FACTS always remain authoritative."""

    result = dict(session or {})
    budget = max(1200, int(character_budget))
    global_text = _summary_text(rolling_summary, min(2200, budget // 3))
    stock_text = _summary_text(stock_summary, min(1600, budget // 4))
    fixed_cost = len(global_text) + len(stock_text)
    remaining = max(600, budget - fixed_cost)
    selected: list[dict[str, str]] = []
    for exchange in reversed(list(recent_exchanges or [])):
        user = _clean_text(exchange.get("user"), 700)
        assistant = _clean_text(exchange.get("assistant"), 1100)
        cost = len(user) + len(assistant)
        if selected and cost > remaining:
            continue
        if not selected and cost > remaining:
            assistant = assistant[: max(200, remaining - len(user))]
            cost = len(user) + len(assistant)
        if cost > remaining:
            continue
        selected.append(
            {
                "user": user,
                "assistant": assistant,
                "stock_code": _clean_text(exchange.get("stock_code"), 8),
                "trade_date": _clean_text(exchange.get("trade_date"), 16),
            }
        )
        remaining -= cost
    selected.reverse()
    if selected:
        result["recent_exchanges"] = selected
        result["last_assistant_answer"] = selected[-1]["assistant"]
    if global_text:
        result["rolling_summary"] = global_text
    if stock_text:
        result["stock_memory"] = {
            "stock_code": _clean_text((stock_summary or {}).get("stock_code"), 8),
            "summary": stock_text,
            "historical_trade_date": _clean_text(
                (stock_summary or {}).get("latest_trade_date"), 16
            ),
        }
    if selected or global_text or stock_text:
        result["memory_contract"] = {
            "historical_context_only": True,
            "current_market_facts_override": True,
            "old_market_values_are_not_current": True,
        }
    return result
