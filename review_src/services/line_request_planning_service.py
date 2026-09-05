from __future__ import annotations

"""Single deterministic owner for LINE multi-scope request planning."""

import re
from typing import Any


REQUEST_PLANNER_VERSION = "line-request-planner-v1"

_SCOPE_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fundamentals", ("基本面", "營收", "獲利", "eps", "roe", "毛利率", "財報", "現金流")),
    ("institutional", ("籌碼", "法人", "外資", "投信", "自營商", "融資", "融券", "主力")),
    ("technical", ("技術面", "技術指標", "rsi", "macd", "均線", "趨勢", "量價")),
    ("valuation", ("估值", "本益比", "pe", "股價淨值比", "pb", "殖利率")),
    ("support_resistance", ("支撐", "壓力", "賣壓", "關卡")),
    ("risk", ("風險", "失效", "最壞", "注意條件")),
    ("current_news", ("最新新聞", "重大新聞", "今日新聞", "新聞", "消息", "法說會", "重大事件")),
    ("global_market", ("美股", "費半", "那斯達克", "納斯達克", "標普", "道瓊", "adr")),
    ("night_market", ("夜盤", "台指期", "台指期貨", "期貨盤後")),
    ("taiwan_market_close", ("昨日收盤", "台股收盤", "大盤", "整體台股", "整體走勢")),
    ("geopolitics", ("地緣政治", "美伊", "台海", "戰爭", "制裁", "關稅")),
)

_FOCUS_FALLBACK = {
    "overview": ("price", "technical", "valuation", "institutional", "risk"),
    "chips": ("institutional",),
    "news": ("current_news",),
    "global_market": ("global_market",),
    "night_market": ("night_market",),
    "support_resistance": ("support_resistance", "risk"),
    "technical_decision": ("technical", "risk"),
}


def _normalized(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _is_excluded(text: str, cues: tuple[str, ...]) -> bool:
    for cue in cues:
        if any(prefix + cue in text for prefix in ("不要", "不看", "排除", "不用", "別看")):
            return True
    return False


def plan_line_request(
    question: str,
    *,
    focus: str,
    has_stock: bool,
) -> dict[str, Any]:
    """Return requested/execution plans without model calls or financial facts."""

    text = _normalized(question)
    included: list[str] = []
    excluded: list[str] = []
    decisions: list[dict[str, Any]] = []
    for scope, cues in _SCOPE_CUES:
        matched = next((cue for cue in cues if cue in text), None)
        if matched is None:
            continue
        if _is_excluded(text, cues):
            excluded.append(scope)
            decisions.append(
                {
                    "scope": scope,
                    "decision": "exclude",
                    "source": "explicit_rule",
                    "confidence": 1.0,
                    "matched_fragment": matched,
                }
            )
            continue
        included.append(scope)
        decisions.append(
            {
                "scope": scope,
                "decision": "include",
                "source": "explicit_rule",
                "confidence": 1.0,
                "matched_fragment": matched,
            }
        )

    if not included:
        included.extend(_FOCUS_FALLBACK.get(str(focus or "overview"), (str(focus or "overview"),)))
        decisions.extend(
            {
                "scope": scope,
                "decision": "include",
                "source": "context_rule",
                "confidence": 1.0,
                "matched_fragment": "",
            }
            for scope in included
        )

    included = [scope for scope in dict.fromkeys(included) if scope not in excluded]
    whole_market = any(scope in included for scope in ("taiwan_market_close", "geopolitics"))
    if has_stock and whole_market:
        route = "mixed_analysis"
    elif has_stock:
        route = "stock_analysis"
    elif whole_market or any(scope in included for scope in ("current_news", "global_market")):
        route = "market_analysis"
    else:
        route = "general_investment"
    comprehensive = any(
        cue in text
        for cue in ("完整分析", "全面分析", "綜合分析", "完整評估", "全面評估", "整體評估")
    ) or len(included) >= 3
    depth = "comprehensive" if comprehensive else "focused"
    requested_plan = {
        "contract_version": "request-plan-v1",
        "route": route,
        "requested_depth": depth,
        "requested_scopes": included,
        "excluded_scopes": list(dict.fromkeys(excluded)),
        "requested_trade_date": None,
        "analysis_cutoff": None,
        "classification_method": "rule_based",
        "router_version": REQUEST_PLANNER_VERSION,
        "scope_decisions": decisions,
        "ambiguous_fragments": [],
    }
    execution_plan = {
        "contract_version": "execution-plan-v1",
        "effective_depth": depth,
        "effective_scopes": list(included),
        "retrieval_mode": "cache_only",
        "delivery_mode": "reply_only",
        "omitted_scopes": [],
        "omission_reasons": {},
        "classification_calls": 0,
        "synthesis_calls_max": 1,
    }
    return {"requested_plan": requested_plan, "execution_plan": execution_plan}
