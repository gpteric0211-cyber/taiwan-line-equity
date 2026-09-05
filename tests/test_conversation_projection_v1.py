from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.conversation_projection_v1 import (  # noqa: E402
    MAX_PROJECTION_TOKENS,
    MAX_RECENT_TURNS,
    build_conversation_projection_v1,
    conservative_token_upper_bound,
)


def test_projection_is_bounded_two_sided_and_marks_old_numbers_historical() -> None:
    exchanges = [
        {
            "user": f"第 {index} 題：聯發科 2026-08-28 收盤 1500 元怎麼看？" + "細節" * 300,
            "assistant": "這是當時資料，不代表現在。" + "說明" * 400,
        }
        for index in range(10)
    ]
    projection = build_conversation_projection_v1(
        {
            "code": "2454",
            "stock_name": "聯發科",
            "last_focus": "technical",
            "position_state": "持有",
            "investment_horizon": "波段",
        },
        recent_exchanges=exchanges,
        rolling_summary={"summary": "先前討論聯發科，使用者在意事件與風險。" * 60},
        resolved_entities=[{"code": "2646", "name": "星宇航空"}],
        comparison_entities=[
            {"code": "2646", "name": "星宇航空"},
            {"code": "2454", "name": "聯發科"},
        ],
        last_analysis_id="analysis-2646",
        last_analysis_cutoff="2026-09-01T14:00:00+08:00",
    )
    assert projection["active_stock"]["code"] == "2646"
    assert [row["code"] for row in projection["comparison_stocks"]] == ["2646", "2454"]
    assert projection["turn_count"] <= MAX_RECENT_TURNS
    assert projection["token_count_upper_bound"] <= MAX_PROJECTION_TOKENS
    assert conservative_token_upper_bound(projection) <= MAX_PROJECTION_TOKENS
    assert projection["historical_conversation_claims"]
    assert all(
        claim["can_replace_canonical_fact"] is False
        for claim in projection["historical_conversation_claims"]
    )
    assert projection["position_state"] == "持有"
    assert projection["investment_horizon"] == "波段"
    assert len(projection["projection_digest"]) == 64
    json.dumps(projection, ensure_ascii=False)


def test_profile_fields_are_not_inferred_when_user_never_stated_them() -> None:
    projection = build_conversation_projection_v1(
        {"code": "2454", "stock_name": "聯發科"},
        recent_exchanges=[],
    )
    assert projection["position_state"] is None
    assert projection["investment_horizon"] is None
    assert projection["can_replace_canonical_facts"] is False
