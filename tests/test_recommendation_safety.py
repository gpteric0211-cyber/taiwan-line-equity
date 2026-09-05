from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from analysis.recommendation_safety import (  # noqa: E402
    apply_safety_cap_to_referee,
    assess_recommendation_safety,
)


TRADE_DATE = "2026-08-25"
CALCULATED_AT = "2026-08-25T14:00:00+08:00"


def _restriction(*types: str, ready: bool = True) -> dict:
    return {
        "ready": ready,
        "items": [{"restriction_type": item} for item in types],
    }


def _assess(
    turnover: float,
    *,
    observed_days: int = 20,
    restriction: dict | None = None,
    corporate: dict | None = None,
    company_size: dict | None = None,
    close_price: float = 100.0,
) -> dict:
    return assess_recommendation_safety(
        trade_date=TRADE_DATE,
        calculated_at=CALCULATED_AT,
        turnover_values=[turnover] * observed_days,
        trading_restriction_context=restriction or _restriction(),
        corporate_action_context=corporate or {"active_window": False},
        company_size_context=company_size or {
            "ready": True,
            "data_date": TRADE_DATE,
            "age_days": 0,
            "paid_in_capital_twd": 2_000_000_000,
            "issued_shares": 100_000_000,
        },
        close_price=close_price,
    )


def test_safety_passes_only_with_complete_liquidity_and_official_checks() -> None:
    result = _assess(150_000_000)

    assert result["status"] == "pass"
    assert result["auto_entry_eligible"] is True
    assert result["analysis_eligible"] is True
    assert result["liquidity"]["observed_days"] == 20
    assert result["trade_date"] == TRADE_DATE
    assert result["calculated_at"] == CALCULATED_AT


def test_turnover_below_thirty_million_is_a_hard_exclusion() -> None:
    result = _assess(29_999_999)

    assert result["status"] == "excluded"
    assert result["hard_blocked"] is True
    assert result["auto_entry_eligible"] is False
    assert result["referee_cap"] == "高風險觀察"


def test_turnover_between_thresholds_is_observation_only() -> None:
    result = _assess(50_000_000)

    assert result["status"] == "liquidity_risk"
    assert result["hard_blocked"] is False
    assert result["analysis_eligible"] is True
    assert result["auto_entry_eligible"] is False
    assert result["referee_cap"] == "警戒"


def test_missing_twenty_day_history_or_official_snapshot_fails_closed() -> None:
    short_history = _assess(150_000_000, observed_days=19)
    delayed_source = _assess(150_000_000, restriction=_restriction(ready=False))

    assert short_history["status"] == "unavailable"
    assert short_history["auto_entry_eligible"] is False
    assert delayed_source["status"] == "unavailable"
    assert delayed_source["trading_restriction"]["status"] == "source_delayed"


def test_missing_or_small_company_size_is_not_auto_recommended() -> None:
    missing = _assess(150_000_000, company_size={"ready": False})
    small = _assess(
        150_000_000,
        company_size={
            "ready": True,
            "data_date": TRADE_DATE,
            "age_days": 0,
            "paid_in_capital_twd": 900_000_000,
            "issued_shares": 40_000_000,
        },
        close_price=100,
    )

    assert missing["status"] == "unavailable"
    assert missing["auto_entry_eligible"] is False
    assert small["status"] == "small_company_risk"
    assert small["company_size"]["estimated_market_cap_twd"] == 4_000_000_000
    assert small["auto_entry_eligible"] is False
    assert small["analysis_eligible"] is True


def test_attention_and_disposition_are_both_hard_blocked() -> None:
    attention = _assess(150_000_000, restriction=_restriction("attention"))
    disposition = _assess(150_000_000, restriction=_restriction("disposition"))

    assert attention["status"] == "restricted"
    assert attention["analysis_eligible"] is False
    assert attention["auto_entry_eligible"] is False
    assert attention["hard_blocked"] is True
    assert disposition["status"] == "restricted"
    assert disposition["analysis_eligible"] is False
    assert disposition["hard_blocked"] is True


def test_corporate_action_window_blocks_conditional_entry() -> None:
    result = _assess(
        150_000_000,
        corporate={
            "active_window": True,
            "label": "除息調整期間",
            "action_date": "2026-08-27",
            "action_type": "right",
            "days_from_action": -2,
            "confirmed": True,
            "adjustment_method": "bonus_share_distribution",
            "stock_distribution_ratio": 1.98279460,
            "ratio_unit": "new_shares_per_existing_share",
            "share_count_factor": 2.98279460,
            "pre_event_price_multiplier": 0.335256071605,
            "verification_status": "official_verified",
            "source_id": "TWSE_TWT48U",
            "source_url": "https://www.twse.com.tw/exchangeReport/TWT48U?date=20260818",
            "available_at": "2026-09-03T10:38:26+08:00",
            "directional_weight_eligible": False,
        },
    )

    assert result["status"] == "corporate_action_window"
    assert result["auto_entry_eligible"] is False
    assert result["referee_cap"] == "警戒"
    action = result["corporate_action"]
    assert action["action_type"] == "right"
    assert action["stock_distribution_ratio"] == 1.98279460
    assert action["ratio_unit"] == "new_shares_per_existing_share"
    assert action["share_count_factor"] == 2.98279460
    assert action["pre_event_price_multiplier"] == 0.335256071605
    assert action["directional_weight_eligible"] is False


def test_safety_cap_cannot_create_a_more_bullish_referee_result() -> None:
    base = {"status": "可觀察", "reasons": ["原裁判理由"]}
    safety = _assess(20_000_000)

    result = apply_safety_cap_to_referee(base, safety)

    assert result["status"] == "高風險觀察"
    assert "流動性" in result["reasons"][0]
    assert base["status"] == "可觀察"
