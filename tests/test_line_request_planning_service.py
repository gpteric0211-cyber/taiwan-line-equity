from __future__ import annotations

import sys
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.line_request_planning_service import plan_line_request  # noqa: E402


def _requested(question: str, *, has_stock: bool = True) -> dict[str, object]:
    return plan_line_request(question, focus="overview", has_stock=has_stock)["requested_plan"]


def test_composite_stock_request_preserves_every_explicit_scope() -> None:
    plan = _requested("台積電基本面、籌碼、技術面與最新新聞完整分析")

    assert plan["requested_depth"] == "comprehensive"
    assert plan["requested_scopes"] == [
        "fundamentals",
        "institutional",
        "technical",
        "current_news",
    ]
    assert plan["classification_method"] == "rule_based"


def test_explicit_news_exclusion_wins_without_dropping_chips() -> None:
    plan = _requested("只看籌碼，不要新聞")

    assert plan["requested_scopes"] == ["institutional"]
    assert plan["excluded_scopes"] == ["current_news"]


def test_stock_and_whole_market_becomes_mixed_analysis() -> None:
    plan = _requested("台積電和整體台股誰比較強")

    assert plan["route"] == "mixed_analysis"
    assert "taiwan_market_close" in plan["requested_scopes"]


def test_market_news_geopolitics_sentence_keeps_all_scopes_without_old_stock() -> None:
    plan = _requested(
        "昨日收盤幫我評估整體走勢及抓取今日重大財經新聞、美伊現況及股市分析",
        has_stock=False,
    )

    assert plan["route"] == "market_analysis"
    assert plan["requested_depth"] == "comprehensive"
    assert set(plan["requested_scopes"]) >= {
        "taiwan_market_close",
        "current_news",
        "geopolitics",
    }
