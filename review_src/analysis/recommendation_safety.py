from __future__ import annotations

import math
from statistics import median
from typing import Any


RECOMMENDATION_SAFETY_VERSION = "recommendation-safety-v3"
TURNOVER_LOOKBACK_DAYS = 20
MIN_AVG_TURNOVER_TWD = 30_000_000.0
LOW_LIQUIDITY_UPPER_TWD = 100_000_000.0
MIN_PAID_IN_CAPITAL_TWD = 1_000_000_000.0
MIN_MARKET_CAP_TWD = 5_000_000_000.0

HARD_RESTRICTION_TYPES = {
    "attention",
    "disposition",
    "altered_trading",
    "periodic_trading",
    "managed_stock",
    "trading_halt",
}

RESTRICTION_LABELS = {
    "attention": "官方注意股票",
    "disposition": "官方處置股票",
    "altered_trading": "變更交易方式",
    "periodic_trading": "分盤交易",
    "managed_stock": "管理股票",
    "trading_halt": "停止交易",
}


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def assess_recommendation_safety(
    *,
    trade_date: str,
    calculated_at: str,
    turnover_values: list[Any] | None,
    trading_restriction_context: dict[str, Any] | None,
    corporate_action_context: dict[str, Any] | None,
    company_size_context: dict[str, Any] | None = None,
    close_price: Any = None,
) -> dict[str, Any]:
    """Decide whether a stock may enter an automatic conditional-entry path.

    This layer contains safety eligibility only.  It does not calculate RSI,
    support/resistance, or a competing directional signal.
    """

    valid_turnovers = [
        number
        for number in (_finite_positive(value) for value in list(turnover_values or []))
        if number is not None
    ][:TURNOVER_LOOKBACK_DAYS]
    liquidity_ready = len(valid_turnovers) >= TURNOVER_LOOKBACK_DAYS
    average_turnover = (
        sum(valid_turnovers[:TURNOVER_LOOKBACK_DAYS]) / TURNOVER_LOOKBACK_DAYS
        if liquidity_ready
        else None
    )
    median_turnover = (
        float(median(valid_turnovers[:TURNOVER_LOOKBACK_DAYS]))
        if liquidity_ready
        else None
    )
    if average_turnover is None:
        liquidity_status = "unavailable"
    elif average_turnover < MIN_AVG_TURNOVER_TWD:
        liquidity_status = "excluded"
    elif average_turnover < LOW_LIQUIDITY_UPPER_TWD:
        liquidity_status = "risk"
    else:
        liquidity_status = "pass"

    restriction = dict(trading_restriction_context or {})
    restriction_ready = restriction.get("ready") is True
    restriction_types = list(
        dict.fromkeys(
            str(item.get("restriction_type") or "").strip()
            for item in list(restriction.get("items") or [])
            if str(item.get("restriction_type") or "").strip()
        )
    )
    hard_restrictions = [item for item in restriction_types if item in HARD_RESTRICTION_TYPES]
    corporate = dict(corporate_action_context or {})
    corporate_action_active = bool(corporate.get("active_window"))
    corporate_action_label = str(corporate.get("label") or "公司行動調整期間")

    company_size = dict(company_size_context or {})
    company_size_ready = company_size.get("ready") is True
    paid_in_capital = _finite_positive(company_size.get("paid_in_capital_twd"))
    issued_shares = _finite_positive(company_size.get("issued_shares"))
    size_close = _finite_positive(close_price)
    market_cap = (
        issued_shares * size_close
        if issued_shares is not None and size_close is not None
        else None
    )
    small_company = bool(
        company_size_ready
        and (
            paid_in_capital is None
            or market_cap is None
            or paid_in_capital < MIN_PAID_IN_CAPITAL_TWD
            or market_cap < MIN_MARKET_CAP_TWD
        )
    )

    blocking_reasons: list[str] = []
    warnings: list[str] = []
    if hard_restrictions:
        labels = [RESTRICTION_LABELS.get(item, item) for item in hard_restrictions]
        blocking_reasons.append("目前列入" + "、".join(labels) + "，排除自動進場判斷")
    if liquidity_status == "excluded":
        blocking_reasons.append("20日平均成交金額低於3,000萬元，流動性不符合自動推薦門檻")
    elif liquidity_status == "risk":
        blocking_reasons.append("20日平均成交金額介於3,000萬元與1億元，只保留觀察、不提供條件式進場")
    elif liquidity_status == "unavailable":
        blocking_reasons.append("20個完整交易日的成交金額不足，安全資格尚未完成")
    if not restriction_ready:
        blocking_reasons.append("官方注意、處置及交易方式名單尚未完整通過同日檢查")
    if corporate_action_active:
        blocking_reasons.append(f"{corporate_action_label}，暫停以未穩定技術指標形成進場判斷")
    if not company_size_ready:
        blocking_reasons.append("官方公司規模快照尚未通過新鮮度與完整性檢查")
    elif small_company:
        blocking_reasons.append("股本低於10億元或推算市值低於50億元，只保留觀察、不進自動推薦")

    hard_blocked = bool(hard_restrictions or liquidity_status == "excluded")
    missing_inputs = bool(not liquidity_ready or not restriction_ready or not company_size_ready)
    risk_only = bool(liquidity_status == "risk" or corporate_action_active or small_company)
    auto_entry_eligible = not (hard_blocked or missing_inputs or risk_only)
    analysis_eligible = not bool(hard_restrictions)

    if hard_restrictions:
        status = "restricted"
        label = "交易限制，不進行自動判斷"
        referee_cap = "高風險觀察"
    elif liquidity_status == "excluded":
        status = "excluded"
        label = "流動性不合格"
        referee_cap = "高風險觀察"
    elif missing_inputs:
        status = "unavailable"
        label = "安全資格資料不足"
        referee_cap = None
    elif small_company:
        status = "small_company_risk"
        label = "公司規模風險，只保留觀察"
        referee_cap = "警戒"
    elif corporate_action_active:
        status = "corporate_action_window"
        label = "公司行動調整期間"
        referee_cap = "警戒"
    elif liquidity_status == "risk":
        status = "liquidity_risk"
        label = "流動性風險，只保留觀察"
        referee_cap = "警戒"
    else:
        status = "pass"
        label = "安全資格通過"
        referee_cap = None

    return {
        "available": not missing_inputs,
        "status": status,
        "label": label,
        "trade_date": trade_date,
        "calculated_at": calculated_at,
        "auto_entry_eligible": auto_entry_eligible,
        "analysis_eligible": analysis_eligible,
        "hard_blocked": hard_blocked,
        "referee_cap": referee_cap,
        "blocking_reasons": list(dict.fromkeys(blocking_reasons))[:5],
        "warnings": warnings,
        "liquidity": {
            "status": liquidity_status,
            "observed_days": len(valid_turnovers),
            "required_days": TURNOVER_LOOKBACK_DAYS,
            "average_turnover_twd": round(average_turnover, 2) if average_turnover is not None else None,
            "median_turnover_twd": round(median_turnover, 2) if median_turnover is not None else None,
            "minimum_twd": MIN_AVG_TURNOVER_TWD,
            "normal_twd": LOW_LIQUIDITY_UPPER_TWD,
        },
        "trading_restriction": {
            "status": "ok" if restriction_ready else "source_delayed",
            "active_types": restriction_types,
            "labels": [RESTRICTION_LABELS.get(item, item) for item in restriction_types],
        },
        "corporate_action": {
            "status": "active_window" if corporate_action_active else "clear",
            "label": corporate_action_label if corporate_action_active else None,
            "action_date": corporate.get("action_date"),
            "action_type": corporate.get("action_type"),
            "days_from_action": corporate.get("days_from_action"),
            "confirmed": corporate.get("confirmed"),
            "adjustment_method": corporate.get("adjustment_method"),
            "stock_distribution_ratio": corporate.get(
                "stock_distribution_ratio"
            ),
            "ratio_unit": corporate.get("ratio_unit"),
            "cash_dividend_per_share": corporate.get(
                "cash_dividend_per_share"
            ),
            "share_count_factor": corporate.get("share_count_factor"),
            "pre_event_price_multiplier": corporate.get(
                "pre_event_price_multiplier"
            ),
            "verification_status": corporate.get("verification_status"),
            "source_id": corporate.get("source_id"),
            "source_url": corporate.get("source_url"),
            "available_at": corporate.get("available_at"),
            "directional_weight_eligible": bool(
                corporate.get("directional_weight_eligible")
            ),
        },
        "company_size": {
            "status": "risk" if small_company else "ok" if company_size_ready else "unavailable",
            "data_date": company_size.get("data_date"),
            "age_days": company_size.get("age_days"),
            "paid_in_capital_twd": round(paid_in_capital, 2) if paid_in_capital is not None else None,
            "estimated_market_cap_twd": round(market_cap, 2) if market_cap is not None else None,
            "minimum_paid_in_capital_twd": MIN_PAID_IN_CAPITAL_TWD,
            "minimum_market_cap_twd": MIN_MARKET_CAP_TWD,
        },
        "can_override_main_status": False,
        "version": RECOMMENDATION_SAFETY_VERSION,
    }


def apply_safety_cap_to_referee(
    base_result: dict[str, Any],
    safety: dict[str, Any] | None,
) -> dict[str, Any]:
    """Conservatively cap a directional referee result without creating one."""

    result = dict(base_result or {})
    safety_data = dict(safety or {})
    cap = str(safety_data.get("referee_cap") or "")
    reasons = [
        str(item)
        for item in list(safety_data.get("blocking_reasons") or [])
        if str(item).strip()
    ]
    if not cap or not reasons:
        return result
    current = str(result.get("status") or "資料不足")
    if cap == "高風險觀察":
        result["status"] = "高風險觀察"
    elif cap == "警戒" and current not in {"高風險觀察", "資料不足"}:
        result["status"] = "警戒"
    result["reasons"] = list(dict.fromkeys([*reasons, *list(result.get("reasons") or [])]))[:2]
    return result
