from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = PROJECT_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.line_bot_config import load_line_bot_env  # noqa: E402


load_dotenv(REVIEW_SRC / ".env", override=False)
load_line_bot_env()

from services import line_bot_service  # noqa: E402
from services.conversation_router_service import classify_ambiguous_turn  # noqa: E402


QUESTIONS = (
    "台積電最近怎麼樣？",
    "如果營收變差呢？",
    "我沒持有，比較偏波段",
    "那要注意哪個條件？",
    "最近市場在炒什麼？",
    "那對台積電的風險呢？",
    "這樣還撐得住嗎？",
)


def _smoke_facts() -> dict[str, Any]:
    """Fixed trusted facts keep this dialogue test independent of API secrets."""

    return {
        "status": "ok",
        "code": "2330",
        "stock": {"name": "台積電", "market": "listed", "exchange": "TWSE"},
        "trade_date": "2026-08-27",
        "freshness": {"status": "current", "ready": True},
        "official_ohlcv": {
            "available": True,
            "official_trusted": True,
            "open": "1000",
            "high": "1020",
            "low": "990",
            "close": "1010",
            "volume_shares": "10000000",
        },
        "technical": {
            "available": True,
            "decision_ready": True,
            "rsi": {"rsi14": "48.0"},
            "macd": {"dif": "1.0", "signal": "0.8", "oscillator": "0.2"},
            "moving_averages": {"ma20": "1005", "ma60": "980"},
            "volume_ma20": "9000000",
        },
        "valuation": {"available": False},
        "referee": {
            "decision_ready": True,
            "main_status": "中性",
            "main_reasons": ["趨勢與動能訊號尚未完全一致"],
            "support_zone": {"label": "990～1005", "strength": "中"},
            "resistance_zone": {"label": "1020～1040", "strength": "中"},
            "can_be_overridden_by_model": False,
        },
        "price_volume": {"decision_ready": False},
        "evidence_summary": {
            "technical_observation": "趨勢與動能訊號尚未完全一致",
            "trend_logic": "收盤仍在中期均線附近",
            "momentum_logic": "RSI14 位於中性區",
            "volume_logic": "量能略高於二十日均量",
            "limits": ["此為固定驗收資料，不代表目前市場行情"],
        },
        "verified_claims": [
            "目前判斷：中性；理由：趨勢與動能訊號尚未完全一致",
            "參考區間：支撐 990～1005（中）／賣壓 1020～1040（中）",
            "RSI14：48.0",
        ],
    }


def _resolve_smoke_stock(query: str) -> dict[str, Any]:
    normalized = str(query or "").replace(" ", "")
    if "台積電" in normalized or "2330" in normalized:
        return {"ok": True, "stock": {"code": "2330", "name": "台積電"}}
    return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}


def _smoke_market_brief(_question: str) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "ok",
        "reference_date": "2026-08-27",
        "topic_match_required": False,
        "topic_match": True,
        "matched_events": [],
        "external_event_context": {
            "available": True,
            "events": [
                {
                    "event_date": "2026-08-27",
                    "publisher": "政府公開資訊",
                    "title": "產業政策測試事件",
                }
            ],
        },
    }


def run() -> dict[str, Any]:
    original_qwen_enabled = os.environ.get("QWEN_ENABLED")
    original_resolve = line_bot_service.resolve_stock_query
    original_fetch_daily = line_bot_service.fetch_daily_market_data
    original_compact_daily = line_bot_service._compact_daily
    original_market_brief = line_bot_service.fetch_market_brief
    os.environ["QWEN_ENABLED"] = "false"
    line_bot_service.resolve_stock_query = _resolve_smoke_stock
    line_bot_service.fetch_daily_market_data = lambda *_args, **_kwargs: {}
    line_bot_service._compact_daily = lambda _payload: _smoke_facts()
    line_bot_service.fetch_market_brief = _smoke_market_brief
    context: dict[str, Any] = {}
    turns: list[dict[str, Any]] = []
    try:
        for question in QUESTIONS:
            result = line_bot_service._answer_stock_question_result(
                question,
                conversation_context=context,
            )
            context = dict(result.context_update or context)
            answer = str(result.text or "")
            turns.append(
                {
                    "question": question,
                    "answer": answer,
                    "code": context.get("code"),
                    "last_focus": context.get("last_focus"),
                    "last_mode": context.get("last_mode"),
                    "position_state": context.get("position_state"),
                    "investment_horizon": context.get("investment_horizon"),
                }
            )
        os.environ["QWEN_ENABLED"] = "true"
        semantic_route = classify_ambiguous_turn(
            "照這個情況還扛得住嗎？",
            context,
            model_enabled=True,
            timeout_seconds=30,
        )
    finally:
        line_bot_service.resolve_stock_query = original_resolve
        line_bot_service.fetch_daily_market_data = original_fetch_daily
        line_bot_service._compact_daily = original_compact_daily
        line_bot_service.fetch_market_brief = original_market_brief
        if original_qwen_enabled is None:
            os.environ.pop("QWEN_ENABLED", None)
        else:
            os.environ["QWEN_ENABLED"] = original_qwen_enabled

    assert turns[0]["code"] == "2330"
    assert turns[1]["last_focus"] == "fundamentals"
    assert turns[2]["position_state"] == "not_holding"
    assert turns[2]["investment_horizon"] == "swing"
    assert turns[3]["last_focus"] == "risk"
    assert turns[4]["last_mode"] == "market"
    assert turns[5]["code"] == "2330"
    assert turns[6]["code"] == "2330"
    assert semantic_route.intent == "active_stock_follow_up"
    assert semantic_route.source == "model"
    forbidden = (
        "請告訴我公司名稱或四碼代號",
        "裁判層",
        "規則樹",
        "內部權重",
        "FACTS",
        "MARKET_FACTS",
    )
    assert all(not any(term in turn["answer"] for term in forbidden) for turn in turns)
    assert all(turn["answer"].strip() for turn in turns)
    return {
        "ok": True,
        "turn_count": len(turns),
        "semantic_route": {
            "intent": semantic_route.intent,
            "confidence": semantic_route.confidence,
            "source": semantic_route.source,
        },
        "turns": turns,
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
