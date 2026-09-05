from __future__ import annotations

"""Conservative low-zone confirmation for downstream advisory use.

This module reuses persisted technical snapshots.  It does not calculate RSI,
replace the project referee, or claim that an exact market bottom is known.
"""

import math
from typing import Any


LOW_ZONE_ENTRY_VERSION = "low-zone-confirmation-v1"
LOW_ZONE_RSI_MIN = 30.0
LOW_ZONE_RSI_MAX = 45.0
MIN_VOLUME_RATIO = 0.5
STRONG_VOLUME_RATIO = 0.8
MIN_REWARD_RISK_RATIO = 1.5
MAX_RISK_PCT = 6.0
MAX_RISK_ATR = 2.0


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _format_number(value: float | None) -> str:
    if value is None:
        return "無資料"
    rounded = round(value, 2)
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:.2f}".rstrip("0").rstrip(".")


def _zone(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    price = _number(raw.get("price"))
    lower = _number(raw.get("zone_low"))
    upper = _number(raw.get("zone_high"))
    lower = lower if lower is not None else price
    upper = upper if upper is not None else price
    if lower is None or upper is None:
        return {"available": False, "lower": None, "upper": None, "label": "無資料"}
    if lower > upper:
        lower, upper = upper, lower
    label = (
        _format_number(lower)
        if abs(upper - lower) < 1e-9
        else f"{_format_number(lower)}～{_format_number(upper)}"
    )
    return {
        "available": True,
        "lower": lower,
        "upper": upper,
        "label": label,
        "strength": str(raw.get("strength") or "") or None,
        "score": _number(raw.get("score")),
    }


def _unavailable(reason_code: str, reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "status": "unavailable",
        "reason_code": reason_code,
        "stage": "unavailable",
        "stage_label": "資料不足",
        "batch_entry_eligible": False,
        "rsi_strategy_applicable": False,
        "summary": reason,
        "blocking_reasons": [reason],
        "evidence": [],
        "can_override_main_status": False,
        "version": LOW_ZONE_ENTRY_VERSION,
    }


def assess_low_zone_entry(
    *,
    referee: dict[str, Any],
    current_price: Any,
    technical: dict[str, Any] | None,
    recent_context: list[dict[str, Any]] | None,
    price_basis: str = "completed_close",
    recommendation_safety: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess whether a low-RSI setup has enough evidence for a first tranche.

    ``recent_context`` must be newest first and contain already-materialized
    RSI/MACD values.  The function deliberately fails closed for intraday price
    mixed with prior-close technicals, unadjusted price discontinuities, or any
    missing confirmation input.
    """

    if price_basis != "completed_close":
        return _unavailable(
            "completed_close_required",
            "盤中價格與盤後 RSI 不可混用；需等完整收盤資料再判斷低檔止跌。",
        )
    if not (referee or {}).get("decision_ready"):
        return _unavailable("referee_not_ready", "裁判層資料不足，不能判斷低檔止跌。")
    technical = technical or {}
    if not technical.get("decision_ready"):
        return _unavailable("technical_not_ready", "同日技術資料不足，不能判斷低檔止跌。")

    rows = [dict(row) for row in list(recent_context or [])[:20] if isinstance(row, dict)]
    if len(rows) < 3:
        return _unavailable(
            "recent_context_not_ready",
            "至少需要最近三個完整交易日的 RSI、MACD 與價格資料。",
        )
    for row in rows[:3]:
        quality = str(row.get("technical_data_quality") or "").lower()
        if not row.get("technical_decision_ready") or quality not in {"ok", "official"}:
            return _unavailable(
                "recent_context_not_ready",
                "最近三個交易日的技術資料尚未全部通過品質檢查。",
            )

    price = _number(current_price)
    current_close = _number(rows[0].get("close"))
    current_open = _number(rows[0].get("open"))
    current_high = _number(rows[0].get("high"))
    current_low = _number(rows[0].get("low"))
    current_volume = _number(rows[0].get("volume"))
    previous_close = _number(rows[1].get("close"))
    previous_low = _number(rows[1].get("low"))
    rsi_values = [
        _number((technical.get("rsi") or {}).get("rsi14")),
        _number(rows[1].get("rsi14")),
        _number(rows[2].get("rsi14")),
    ]
    persisted_current_rsi = _number(rows[0].get("rsi14"))
    macd_current = _number((technical.get("macd") or {}).get("oscillator"))
    macd_previous = _number(rows[1].get("macd_osc"))
    atr14 = _number(technical.get("atr14"))
    volume_ma20 = _number(technical.get("volume_ma20"))
    previous_10d_low = _number(technical.get("previous_10d_low"))
    required = [
        price,
        current_close,
        current_open,
        current_high,
        current_low,
        current_volume,
        previous_close,
        previous_low,
        *rsi_values,
        persisted_current_rsi,
        macd_current,
        macd_previous,
        atr14,
        volume_ma20,
        previous_10d_low,
    ]
    if any(value is None for value in required):
        return _unavailable(
            "confirmation_inputs_missing",
            "止跌判斷需要的 RSI、MACD、OHLCV、ATR 或前低資料不完整。",
        )
    assert price is not None
    assert current_close is not None
    assert current_open is not None
    assert current_high is not None
    assert current_low is not None
    assert current_volume is not None
    assert previous_close is not None
    assert previous_low is not None
    assert persisted_current_rsi is not None
    assert macd_current is not None
    assert macd_previous is not None
    assert atr14 is not None
    assert volume_ma20 is not None
    assert previous_10d_low is not None
    rsi_current, rsi_previous, rsi_two_days_ago = rsi_values
    assert rsi_current is not None
    assert rsi_previous is not None
    assert rsi_two_days_ago is not None
    if min(price, current_close, current_high, current_low, atr14, volume_ma20, previous_10d_low) <= 0:
        return _unavailable("invalid_numeric_input", "止跌判斷的價格或量能基準無效。")
    if current_volume < 0 or not all(0 <= value <= 100 for value in rsi_values):
        return _unavailable("invalid_numeric_input", "止跌判斷的 RSI 或成交量數值無效。")
    if abs(price - current_close) / current_close > 0.001:
        return _unavailable(
            "price_basis_mismatch",
            "判斷價格與技術指標的完整收盤日不一致。",
        )
    if abs(rsi_current - persisted_current_rsi) > 0.05:
        return _unavailable("rsi_snapshot_mismatch", "同日 RSI 快照不一致，已停止判斷。")

    support = _zone((referee or {}).get("support_zone"))
    resistance = _zone((referee or {}).get("resistance_zone"))
    if not support.get("available") or not resistance.get("available"):
        return _unavailable("zones_not_ready", "支撐與賣壓區未同時形成，不能判斷分批條件。")
    support_low = float(support["lower"])
    support_high = float(support["upper"])
    resistance_low = float(resistance["lower"])
    resistance_high = float(resistance["upper"])

    recent_closes = [_number(row.get("close")) for row in rows]
    price_discontinuity = any(
        left is not None
        and right is not None
        and right > 0
        and abs(left / right - 1.0) > 0.20
        for left, right in zip(recent_closes, recent_closes[1:])
    )
    support_quality_ok = bool(
        support.get("strength") in {"中", "強"}
        or (support.get("score") is not None and float(support["score"]) >= 4.0)
    )
    support_allowance = min(price * 0.01, atr14 * 0.5)
    support_held = price >= support_low and current_close >= previous_10d_low
    near_support = support_held and price <= support_high + support_allowance
    near_resistance = price >= resistance_low * 0.99
    rsi_low_zone = LOW_ZONE_RSI_MIN <= rsi_current <= LOW_ZONE_RSI_MAX
    rsi_turn_up = rsi_current > rsi_previous
    rsi_rising_two_sessions = rsi_current > rsi_previous > rsi_two_days_ago
    rsi_declining_three_sessions = rsi_current < rsi_previous < rsi_two_days_ago
    macd_improving = macd_current > macd_previous
    close_location = (
        (current_close - current_low) / (current_high - current_low)
        if current_high > current_low
        else 1.0 if current_close >= current_open else 0.0
    )
    price_reversal = bool(
        current_close >= previous_close
        and (current_close > current_open or close_location >= 0.60)
    )
    volume_ratio = current_volume / volume_ma20
    volume_minimum_ok = volume_ratio >= MIN_VOLUME_RATIO
    volume_strong = volume_ratio >= STRONG_VOLUME_RATIO
    down_volume_break = bool(
        current_close < previous_close
        and current_low < previous_low
        and volume_ratio >= 1.20
    )
    confirmation_count = sum((rsi_turn_up, macd_improving, price_reversal))

    raw_support_risk = price - support_low
    risk_distance = max(raw_support_risk, atr14 * 0.5) if raw_support_risk >= 0 else raw_support_risk
    atr_risk_floor_used = bool(raw_support_risk >= 0 and risk_distance > raw_support_risk)
    reward_distance = resistance_high - price
    risk_pct = (risk_distance / price * 100) if risk_distance > 0 else None
    risk_atr = (risk_distance / atr14) if risk_distance > 0 else None
    reward_risk_ratio = (
        reward_distance / risk_distance
        if risk_distance > 0 and reward_distance > 0
        else None
    )
    reward_risk_ok = bool(
        reward_risk_ratio is not None
        and reward_risk_ratio >= MIN_REWARD_RISK_RATIO
        and risk_pct is not None
        and risk_pct <= MAX_RISK_PCT
        and risk_atr is not None
        and risk_atr <= MAX_RISK_ATR
    )

    hard_blockers: list[str] = []
    referee_status = str((referee or {}).get("main_status") or "資料不足")
    if price_discontinuity:
        hard_blockers.append("近幾日價格有超過 20% 斷層，可能涉及公司行動或未還原資料")
    if referee_status == "高風險觀察":
        hard_blockers.append("唯一裁判層仍為高風險觀察")
    if price < support_low:
        hard_blockers.append(f"收盤已跌破支撐下緣 {support['label']}")
    if current_close < previous_10d_low:
        hard_blockers.append("收盤已跌破前 10 日低點")
    if down_volume_break:
        hard_blockers.append("價格下跌且量比達 1.2，仍有放量破低風險")

    blockers = list(hard_blockers)
    safety = dict(recommendation_safety or {})
    if safety and not safety.get("auto_entry_eligible"):
        safety_reasons = [
            str(item)
            for item in list(safety.get("blocking_reasons") or [])
            if str(item).strip()
        ]
        blockers.extend(safety_reasons or ["安全資格尚未通過，不啟動低檔分批"])
    if rsi_current < LOW_ZONE_RSI_MIN:
        blockers.append("RSI14 低於 30，仍屬極弱區，不能把超賣直接當成底部")
    elif rsi_current > LOW_ZONE_RSI_MAX:
        blockers.append("RSI14 不在 30～45 低檔觀察帶")
    if rsi_declining_three_sessions:
        blockers.append("RSI14 最近三個交易日仍連續下滑")
    if not support_quality_ok:
        blockers.append("支撐強度尚未達中等以上")
    if not near_support:
        blockers.append("收盤未落在支撐區或其上方 1% 內")
    if near_resistance:
        blockers.append("收盤已接近首道賣壓，不適合作為低檔第一批")
    if not rsi_turn_up:
        blockers.append("RSI14 尚未較前一交易日回升")
    if confirmation_count < 2:
        blockers.append("RSI、MACD 與價格止跌訊號未達三項中的兩項")
    if referee_status == "警戒" and confirmation_count < 3:
        blockers.append("裁判層仍為警戒，需 RSI、MACD 與價格三項同時改善")
    if rsi_current < 35 and confirmation_count < 3:
        blockers.append("RSI14 位於 30～35 較弱區，需三項止跌訊號同時改善")
    if not volume_minimum_ok:
        blockers.append("成交量低於 20 日均量 50%，止跌確認度不足")
    if not reward_risk_ok:
        blockers.append("至首道賣壓區上緣的報酬風險比或停損距離未達門檻")
    if referee_status not in {"中性", "可觀察", "警戒"}:
        blockers.append("目前裁判主狀態不適用低檔分批策略")

    eligible = bool(
        not blockers
        and rsi_low_zone
        and rsi_turn_up
        and confirmation_count >= 2
        and support_quality_ok
        and near_support
        and volume_minimum_ok
        and reward_risk_ok
    )
    if hard_blockers:
        stage = "invalidated"
        stage_label = "風險條件未通過"
    elif safety and not safety.get("auto_entry_eligible"):
        stage = "safety_not_eligible"
        stage_label = "安全資格未通過"
    elif rsi_current < LOW_ZONE_RSI_MIN:
        stage = "extreme_oversold_wait"
        stage_label = "極弱區，尚未止跌"
    elif rsi_current > LOW_ZONE_RSI_MAX:
        stage = "not_low_zone"
        stage_label = "不屬低檔策略"
    elif eligible:
        stage = "batch_entry_ready"
        stage_label = "初步止跌，可條件式第一批"
    elif near_support and confirmation_count >= 2:
        stage = "stabilizing"
        stage_label = "低檔止穩中，尚待確認"
    else:
        stage = "low_zone_unconfirmed"
        stage_label = "低檔但尚未止跌"

    rsi_direction = (
        "連續兩日回升"
        if rsi_rising_two_sessions
        else "較前一日回升"
        if rsi_turn_up
        else "連續三日下滑"
        if rsi_declining_three_sessions
        else "尚未形成回升"
    )
    evidence: list[str] = [
        f"RSI14 {_format_number(rsi_current)}（{rsi_direction}）",
        f"支撐 {support['label']}（{'守住' if support_held else '跌破'}）",
        "MACD 柱狀體改善" if macd_improving else "MACD 柱狀體尚未改善",
        "收盤出現止穩" if price_reversal else "價格尚未形成止穩 K 棒",
        f"量比 {_format_number(volume_ratio)}",
    ]
    if reward_risk_ratio is not None:
        evidence.append(f"報酬風險比 {_format_number(reward_risk_ratio)}")

    if eligible:
        summary = (
            f"RSI14 {_format_number(rsi_current)} 位於 30～45 低檔觀察帶並{rsi_direction}；"
            f"價格守在 {support['label']} 支撐附近，"
            f"{'MACD 柱狀體改善' if macd_improving else 'MACD 尚待改善'}且收盤止穩，"
            f"報酬風險比約 {_format_number(reward_risk_ratio)}（下方風險至少以 0.5 ATR 估算）。"
            "這只代表可評估小比例第一批，不代表已確認最低點。"
        )
    else:
        leading_reason = blockers[0] if blockers else "止跌證據尚未達完整門檻"
        summary = (
            f"RSI14 {_format_number(rsi_current)}（{rsi_direction}），目前為「{stage_label}」；"
            f"關鍵原因：{leading_reason}。RSI 低本身不等於已到最低點。"
        )

    return {
        "available": True,
        "status": "ok" if eligible else "not_ready",
        "reason_code": None if eligible else stage,
        "stage": stage,
        "stage_label": stage_label,
        "batch_entry_eligible": eligible,
        "rsi_strategy_applicable": rsi_current <= LOW_ZONE_RSI_MAX,
        "rsi14": round(rsi_current, 2),
        "rsi_recent": [round(value, 2) for value in (rsi_current, rsi_previous, rsi_two_days_ago)],
        "rsi_direction": rsi_direction,
        "rsi_turn_up": rsi_turn_up,
        "rsi_rising_two_sessions": rsi_rising_two_sessions,
        "rsi_declining_three_sessions": rsi_declining_three_sessions,
        "support_held": support_held,
        "near_support": near_support,
        "support_quality_ok": support_quality_ok,
        "near_resistance": near_resistance,
        "macd_improving": macd_improving,
        "price_reversal": price_reversal,
        "confirmation_count": confirmation_count,
        "required_confirmation_count": 2,
        "volume_ratio": round(volume_ratio, 2),
        "volume_confirmation": "strong" if volume_strong else "minimum" if volume_minimum_ok else "insufficient",
        "reward_risk_ratio": round(reward_risk_ratio, 2) if reward_risk_ratio is not None else None,
        "risk_pct": round(risk_pct, 2) if risk_pct is not None else None,
        "risk_atr": round(risk_atr, 2) if risk_atr is not None else None,
        "atr_risk_floor_used": atr_risk_floor_used,
        "support": support,
        "resistance": resistance,
        "confirmation_price": current_high,
        "summary": summary,
        "blocking_reasons": blockers[:4],
        "evidence": evidence,
        "first_batch_condition": (
            f"第一批只在收盤續守 {support['label']} 且 RSI14 不再轉弱時小比例評估。"
            if eligible
            else "目前不啟動第一批；先等待缺少的止跌條件補齊。"
        ),
        "second_batch_condition": (
            f"下一交易日 RSI14 與 MACD 柱狀體續改善，且收盤越過本日高點 {_format_number(current_high)}，再評估第二批。"
        ),
        "final_batch_condition": (
            f"其餘批次只在站回月線或帶量突破賣壓 {resistance['label']} 後再評估。"
        ),
        "invalidation": f"收盤跌破支撐下緣 {_format_number(support_low)} 即取消後續批次。",
        "can_override_main_status": False,
        "recommendation_safety_status": safety.get("status") if safety else None,
        "version": LOW_ZONE_ENTRY_VERSION,
    }
