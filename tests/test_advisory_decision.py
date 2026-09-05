from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from analysis.advisory_decision import build_conditional_advisory
from analysis.low_zone_entry import assess_low_zone_entry
from services import bot_market_data_service
from services.bot_market_data_service import _current_intraday_quote, _global_market_context_payload
from services.global_market_snapshot_service import refresh_global_market_snapshot


def _referee(status: str, *, support=(95.0, 98.0), resistance=(104.0, 106.0)) -> dict:
    return {
        "decision_ready": True,
        "main_status": status,
        "main_reasons": ["測試用裁判理由"],
        "support_zone": {"zone_low": support[0], "zone_high": support[1], "strength": "強"},
        "resistance_zone": {"zone_low": resistance[0], "zone_high": resistance[1], "strength": "中"},
        "can_be_overridden_by_model": False,
    }


def _technical(*, rsi14: float, oscillator: float) -> dict:
    return {
        "rsi": {"rsi14": rsi14},
        "macd": {"oscillator": oscillator},
    }


def _low_zone_technical(*, rsi14: float = 40.0, oscillator: float = -0.4) -> dict:
    return {
        "decision_ready": True,
        "rsi": {"rsi14": rsi14},
        "macd": {"oscillator": oscillator},
        "atr14": 3.0,
        "volume_ma20": 1_000.0,
        "previous_10d_low": 95.0,
    }


def _recent_low_zone_rows(
    *,
    current_rsi: float = 40.0,
    previous_rsi: float = 38.0,
    two_days_ago_rsi: float = 37.0,
    current_oscillator: float = -0.4,
    previous_oscillator: float = -0.8,
) -> list[dict]:
    common = {"technical_decision_ready": 1, "technical_data_quality": "ok"}
    return [
        {
            **common,
            "date": "2026-08-25",
            "open": 97.0,
            "high": 99.0,
            "low": 96.5,
            "close": 98.5,
            "volume": 900.0,
            "rsi14": current_rsi,
            "macd_osc": current_oscillator,
        },
        {
            **common,
            "date": "2026-08-24",
            "open": 96.0,
            "high": 98.0,
            "low": 95.5,
            "close": 97.0,
            "volume": 800.0,
            "rsi14": previous_rsi,
            "macd_osc": previous_oscillator,
        },
        {
            **common,
            "date": "2026-08-21",
            "open": 96.0,
            "high": 97.0,
            "low": 95.0,
            "close": 96.0,
            "volume": 850.0,
            "rsi14": two_days_ago_rsi,
            "macd_osc": -1.0,
        },
    ]


def test_low_zone_requires_rsi_support_price_macd_volume_and_reward_risk() -> None:
    recent = _recent_low_zone_rows()
    result = build_conditional_advisory(
        referee=_referee("警戒", support=(95.0, 98.0), resistance=(104.0, 110.0)),
        current_price=98.5,
        technical=_low_zone_technical(),
        previous_macd_osc=-0.8,
        recent_context=recent,
        price_basis="completed_close",
    )

    assessment = result["low_zone_assessment"]
    assert assessment["batch_entry_eligible"] is True
    assert assessment["stage"] == "batch_entry_ready"
    assert assessment["confirmation_count"] == 3
    assert assessment["reward_risk_ratio"] >= 1.5
    assert assessment["can_override_main_status"] is False
    assert result["referee_status"] == "警戒"
    assert result["action_state"] == "低檔止跌，可條件式第一批"
    assert "不代表已確認最低點" in result["headline"]


def test_low_rsi_still_falling_is_not_a_batch_entry() -> None:
    recent = _recent_low_zone_rows(
        current_rsi=38.0,
        previous_rsi=39.0,
        two_days_ago_rsi=40.0,
    )
    result = build_conditional_advisory(
        referee=_referee("中性", support=(95.0, 98.0), resistance=(104.0, 110.0)),
        current_price=98.5,
        technical=_low_zone_technical(rsi14=38.0),
        previous_macd_osc=-0.8,
        recent_context=recent,
    )

    assessment = result["low_zone_assessment"]
    assert assessment["batch_entry_eligible"] is False
    assert assessment["rsi_declining_three_sessions"] is True
    assert result["action_state"] == "低檔尚未止跌，等待確認"
    assert "RSI 低本身不等於已到最低點" in result["headline"]


def test_extreme_oversold_is_not_treated_as_a_confirmed_bottom() -> None:
    recent = _recent_low_zone_rows(
        current_rsi=22.0,
        previous_rsi=20.0,
        two_days_ago_rsi=19.0,
    )
    assessment = assess_low_zone_entry(
        referee=_referee("可觀察", support=(95.0, 98.0), resistance=(104.0, 110.0)),
        current_price=98.5,
        technical=_low_zone_technical(rsi14=22.0),
        recent_context=recent,
    )

    assert assessment["batch_entry_eligible"] is False
    assert assessment["stage"] == "extreme_oversold_wait"
    assert any("不能把超賣直接當成底部" in reason for reason in assessment["blocking_reasons"])


def test_recent_price_discontinuity_blocks_false_low_rsi_signal() -> None:
    recent = _recent_low_zone_rows()
    recent.append(
        {
            "date": "2026-08-20",
            "open": 48.0,
            "high": 51.0,
            "low": 47.0,
            "close": 50.0,
            "volume": 900.0,
            "rsi14": 50.0,
            "macd_osc": 0.0,
            "technical_decision_ready": 1,
            "technical_data_quality": "ok",
        }
    )
    assessment = assess_low_zone_entry(
        referee=_referee("可觀察", support=(95.0, 98.0), resistance=(104.0, 110.0)),
        current_price=98.5,
        technical=_low_zone_technical(),
        recent_context=recent,
    )

    assert assessment["batch_entry_eligible"] is False
    assert assessment["stage"] == "invalidated"
    assert any("價格有超過 20% 斷層" in reason for reason in assessment["blocking_reasons"])


def test_atr_risk_floor_prevents_artificially_high_reward_risk() -> None:
    technical = _low_zone_technical()
    technical["atr14"] = 10.0
    assessment = assess_low_zone_entry(
        referee=_referee("可觀察", support=(95.0, 98.0), resistance=(104.0, 105.0)),
        current_price=98.5,
        technical=technical,
        recent_context=_recent_low_zone_rows(),
    )

    assert assessment["atr_risk_floor_used"] is True
    assert assessment["reward_risk_ratio"] == 1.3
    assert assessment["batch_entry_eligible"] is False
    assert any("報酬風險比" in reason for reason in assessment["blocking_reasons"])


def test_warning_near_support_can_be_conditional_entry_when_momentum_improves() -> None:
    result = build_conditional_advisory(
        referee=_referee("警戒"),
        current_price=97,
        technical=_technical(rsi14=39, oscillator=-0.4),
        previous_macd_osc=-0.8,
    )

    assert result["decision_ready"] is True
    assert result["referee_status"] == "警戒"
    assert result["action_state"] == "止跌確認後小比例試單"
    assert "不必把下跌一律視為不能買" in result["headline"]
    assert any("最近完整收盤價相對位置" in item for item in result["evidence"])
    assert result["can_override_main_status"] is False


def test_high_risk_below_support_prioritizes_exposure_control() -> None:
    result = build_conditional_advisory(
        referee=_referee("高風險觀察"),
        current_price=92,
        technical=_technical(rsi14=30, oscillator=-2),
        previous_macd_osc=-1,
    )

    assert result["action_state"] == "暫緩新增部位"
    assert "重新站回" in result["buy_plan"]
    assert "降低曝險" in result["holder_plan"]


def test_external_and_estimated_cost_context_never_override_referee() -> None:
    result = build_conditional_advisory(
        referee=_referee("中性"),
        current_price=97,
        technical=_technical(rsi14=48, oscillator=0.1),
        previous_macd_osc=0.0,
        global_market_context={"available": True, "note": "美股背景偏弱。"},
        institutional_context={"available": True, "note": "外資估算成本可用。"},
    )

    assert result["referee_status"] == "中性"
    assert result["background_notes"] == ["美股背景偏弱。", "外資估算成本可用。"]
    assert result["can_override_main_status"] is False


def test_missing_referee_fails_closed() -> None:
    result = build_conditional_advisory(
        referee={"decision_ready": False, "main_status": "資料不足"},
        current_price=100,
    )

    assert result["decision_ready"] is False
    assert result["action_state"] == "資料不足"
    assert "不能可靠判斷" in result["headline"]


def test_safety_failure_has_priority_over_a_low_zone_entry_signal() -> None:
    result = build_conditional_advisory(
        referee=_referee("可觀察", support=(95.0, 98.0), resistance=(104.0, 110.0)),
        current_price=98.5,
        technical=_low_zone_technical(),
        previous_macd_osc=-0.8,
        recent_context=_recent_low_zone_rows(),
        recommendation_safety={
            "available": True,
            "status": "liquidity_risk",
            "label": "流動性風險，只保留觀察",
            "auto_entry_eligible": False,
            "hard_blocked": False,
            "blocking_reasons": ["20日平均成交金額不足1億元，只保留觀察"],
        },
    )

    assert result["action_state"] == "僅供觀察，暫緩新增部位"
    assert result["recommendation_safety"]["auto_entry_eligible"] is False
    assert "流動性" in result["headline"]


def test_hard_safety_veto_outputs_no_referee_judgment() -> None:
    result = build_conditional_advisory(
        referee={"decision_ready": False, "main_status": "不判斷"},
        current_price=98.5,
        recommendation_safety={
            "available": True,
            "status": "restricted",
            "hard_blocked": True,
            "auto_entry_eligible": False,
            "blocking_reasons": ["目前列入官方注意股票，排除自動進場判斷"],
        },
    )

    assert result["decision_ready"] is False
    assert result["referee_status"] == "不判斷"
    assert result["action_state"] == "安全條件否決，不判斷"
    assert "官方交易限制" in result["headline"]


def test_price_above_resistance_is_not_misclassified_as_still_near_resistance() -> None:
    result = build_conditional_advisory(
        referee=_referee("可觀察"),
        current_price=110,
        technical=_technical(rsi14=55, oscillator=0.5),
        previous_macd_osc=0.2,
    )

    assert result["price_position"] == "above_resistance"
    assert result["action_state"] == "收盤突破，等待站穩確認"
    assert "後續是否站穩" in result["buy_plan"]


def test_intraday_price_above_resistance_still_waits_for_close_confirmation() -> None:
    result = build_conditional_advisory(
        referee=_referee("可觀察"),
        current_price=110,
        technical=_technical(rsi14=55, oscillator=0.5),
        previous_macd_osc=0.2,
        price_basis="intraday",
    )

    assert result["price_position"] == "above_resistance"
    assert result["action_state"] == "盤中突破，等待收盤確認"
    assert "收盤站穩" in result["buy_plan"]
    assert any("盤中現價相對位置" in item for item in result["evidence"])


def test_missing_intraday_price_preserves_ready_referee_and_names_exact_gap() -> None:
    result = build_conditional_advisory(
        referee=_referee("中性"),
        current_price=None,
        price_basis="unavailable_current_session",
    )

    assert result["decision_ready"] is False
    assert result["referee_status"] == "中性"
    assert result["reason_code"] == "intraday_price_not_ready"
    assert result["action_state"] == "盤中現價暫時不可用"
    assert "最近完整收盤分析已完成" in result["headline"]
    assert "官方日線、技術資料與支撐賣壓" not in result["buy_plan"]


def test_current_session_quote_must_be_fresh_before_it_can_drive_advisory(monkeypatch) -> None:
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    monkeypatch.setattr(
        bot_market_data_service,
        "tw_market_session_now",
        lambda: {"session": "regular"},
    )
    current = _current_intraday_quote(
        "2449",
        {
            "price": 239,
            "high": 243,
            "low": 231,
            "updated_at": now.isoformat(timespec="seconds"),
            "data_status": "ok",
        },
        allow_network=False,
    )
    stale = _current_intraday_quote(
        "2449",
        {
            "price": 232,
            "updated_at": (now - timedelta(hours=1)).isoformat(timespec="seconds"),
            "data_status": "ok",
        },
        allow_network=False,
    )

    assert current["available"] is True
    assert current["price"] == 239
    assert stale["available"] is False
    assert stale["required"] is True
    assert stale["status"] == "source_delayed"


def test_global_close_context_is_quality_gated_and_non_overriding() -> None:
    snapshot = {
        "global_market_rows": [
            {"market_date": "2026-08-21", "ticker": "^GSPC", "change_pct": -0.8, "source_quality": "supplemental"},
            {"market_date": "2026-08-21", "ticker": "^IXIC", "change_pct": -1.1, "source_quality": "supplemental"},
            {"market_date": "2026-08-21", "ticker": "^SOX", "change_pct": -1.7, "source_quality": "supplemental"},
            {"market_date": "2026-08-21", "ticker": "TSM", "change_pct": -1.2, "source_quality": "supplemental"},
        ]
    }

    result = _global_market_context_payload(snapshot, "2026-08-24", "24")

    assert result["available"] is True
    assert result["stance"] == "negative"
    assert result["coverage_count"] == 3
    assert result["industry_code"] == "24"
    assert result["can_override_main_status"] is False


def test_global_snapshot_update_dry_run_is_bounded_and_does_not_write() -> None:
    def fake_quote(ticker: str) -> dict:
        return {
            "ok": True,
            "date": "2026-08-21",
            "price": 100.0,
            "previous_close": 99.0,
            "change_pct": 1.01,
            "currency": "USD",
            "source": "test source",
        }

    result = refresh_global_market_snapshot(dry_run=True, fetch_quote=fake_quote)

    assert result["ok"] is True
    assert result["rows_fetched"] == 13
    assert result["rows_written"] == 0
    assert result["source_quality"] == "supplemental"
