from __future__ import annotations

"""Pure, conditional decision guidance derived from the project referee.

This module does not calculate a competing stock signal.  It translates the
single project referee status into holder/non-holder scenarios and keeps every
external/chip input as a non-overriding background factor.
"""

import math
from typing import Any

from analysis.low_zone_entry import assess_low_zone_entry


ADVISORY_DECISION_VERSION = "referee-conditional-advisory-v3"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _format_price(value: float | None) -> str:
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
    label = _format_price(lower) if abs(upper - lower) < 1e-9 else f"{_format_price(lower)}～{_format_price(upper)}"
    return {
        "available": True,
        "lower": lower,
        "upper": upper,
        "label": label,
        "strength": str(raw.get("strength") or "") or None,
    }


def build_conditional_advisory(
    *,
    referee: dict[str, Any],
    current_price: Any,
    technical: dict[str, Any] | None = None,
    previous_macd_osc: Any = None,
    institutional_context: dict[str, Any] | None = None,
    global_market_context: dict[str, Any] | None = None,
    taifex_night_context: dict[str, Any] | None = None,
    official_event_context: dict[str, Any] | None = None,
    external_event_context: dict[str, Any] | None = None,
    recent_context: list[dict[str, Any]] | None = None,
    price_basis: str = "completed_close",
    recommendation_safety: dict[str, Any] | None = None,
    decision_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a downstream action framework without changing the referee verdict."""

    status = str((referee or {}).get("main_status") or "資料不足")
    price = _number(current_price)
    support = _zone((referee or {}).get("support_zone"))
    resistance = _zone((referee or {}).get("resistance_zone"))
    referee_ready = bool((referee or {}).get("decision_ready"))
    ready = bool(
        referee_ready
        and price is not None
        and price > 0
        and support.get("available")
        and resistance.get("available")
    )
    safety = dict(recommendation_safety or {})
    audit = dict(decision_audit or {})
    audit_note = (
        f"判斷依據為 {audit.get('trade_date')} 完整收盤資料；"
        f"計算時間 {audit.get('calculated_at')}。"
        if audit.get("trade_date") and audit.get("calculated_at")
        else ""
    )
    base = {
        "decision_ready": ready,
        "version": ADVISORY_DECISION_VERSION,
        "source": "shared_project_referee",
        "referee_status": status if referee_ready else "資料不足",
        "reason_code": (
            None
            if ready
            else str((referee or {}).get("reason_code") or "advisory_input_not_ready")
        ),
        "can_override_main_status": False,
        "support": support,
        "resistance": resistance,
        "recommendation_safety": safety,
        "decision_audit": audit,
        "audit_note": audit_note,
    }
    if safety.get("hard_blocked"):
        safety_reasons = [
            str(item)
            for item in list(safety.get("blocking_reasons") or [])
            if str(item).strip()
        ]
        return {
            **base,
            "decision_ready": False,
            "referee_status": "不判斷",
            "action_state": "安全條件否決，不判斷",
            "headline": "目前命中官方交易限制或最低流動性否決條件，不進行技術進場裁判。",
            "buy_plan": "；".join(safety_reasons[:2]) or "安全否決解除後再重新評估。",
            "holder_plan": "若已有部位，先依個人風險上限處理；本結果不提供新增部位條件。",
            "invalidation": "安全否決解除且同日資料重新通過後，才重新建立技術條件。",
            "evidence": safety_reasons[:2],
            "background_notes": [],
        }
    if not ready:
        if referee_ready and price_basis == "unavailable_current_session":
            return {
                **base,
                "reason_code": "intraday_price_not_ready",
                "action_state": "盤中現價暫時不可用",
                "headline": "最近完整收盤分析已完成，但盤中即時成交價尚未取得或不夠新鮮；目前只能提供盤後條件規劃，不能判斷此刻價位。",
                "buy_plan": "先依最近完整收盤的支撐與賣壓規劃觀察；等即時成交價恢復後，再確認當下位於支撐、區間或賣壓附近。",
                "holder_plan": "若已有部位，先沿用最近完整收盤分析的失效條件，不用缺少的盤中價格追加判斷。",
                "invalidation": "盤中即時成交價恢復且通過新鮮度檢查後，再更新當下位置判斷。",
                "evidence": [
                    str(item)
                    for item in list((referee or {}).get("main_reasons") or [])[:2]
                    if str(item).strip()
                ],
                "background_notes": [],
            }
        return {
            **base,
            "action_state": "資料不足",
            "headline": "目前資料不足，不能可靠判斷是否適合進場或調節。",
            "buy_plan": "先等待官方日線、技術資料與支撐賣壓同日通過品質檢查。",
            "holder_plan": "若已有部位，先依自己的原始風險上限控管，不用不完整資料追加判斷。",
            "invalidation": "資料補齊前不設定推測性失效價。",
            "evidence": [],
            "background_notes": [],
        }

    support_low = float(support["lower"])
    support_high = float(support["upper"])
    resistance_low = float(resistance["lower"])
    resistance_high = float(resistance["upper"])
    at_support = support_low <= price <= support_high * 1.01
    below_support = price < support_low
    near_resistance = resistance_low * 0.99 <= price <= resistance_high * 1.005
    above_resistance = price > resistance_high * 1.005

    technical = technical or {}
    rsi14 = _number((technical.get("rsi") or {}).get("rsi14"))
    macd_osc = _number((technical.get("macd") or {}).get("oscillator"))
    prior_osc = _number(previous_macd_osc)
    momentum_improving = bool(
        macd_osc is not None and prior_osc is not None and macd_osc > prior_osc
    )
    momentum_weak = bool(
        rsi14 is not None
        and rsi14 < 45
        and macd_osc is not None
        and macd_osc < 0
        and (prior_osc is None or macd_osc <= prior_osc)
    )
    low_zone_assessment = assess_low_zone_entry(
        referee=referee,
        current_price=price,
        technical=technical,
        recent_context=recent_context,
        price_basis=price_basis,
        recommendation_safety=safety,
    )

    safety_entry_blocked = bool(safety and not safety.get("auto_entry_eligible"))
    if safety_entry_blocked:
        safety_status = str(safety.get("status") or "unavailable")
        if safety_status == "unavailable":
            action_state = "安全資格資料不足"
            headline = "官方交易限制、20日流動性或公司規模資料尚未完整，暫不形成條件式進場判斷。"
        elif safety.get("hard_blocked"):
            action_state = "安全條件未通過"
            headline = "目前未通過官方交易限制或最低流動性門檻，排除自動進場判斷。"
        else:
            action_state = "僅供觀察，暫緩新增部位"
            headline = "目前有流動性、公司規模或公司行動風險，只保留資料觀察，不啟動條件式進場。"
        safety_reasons = [
            str(item)
            for item in list(safety.get("blocking_reasons") or [])
            if str(item).strip()
        ]
        buy_plan = "；".join(safety_reasons[:2]) or "待安全資格完整通過後，再重新評估分批條件。"
    elif status in {"高風險觀察"} or below_support:
        action_state = "暫緩新增部位"
        headline = "目前先不要急著新增部位；應等價格重新站回支撐、弱勢訊號停止惡化後再評估。"
        buy_plan = f"若未持有，至少等收盤重新站回 {support['label']}；未站回前不把價格下跌直接當成便宜。"
    elif above_resistance:
        if price_basis == "completed_close":
            action_state = "收盤突破，等待站穩確認"
            headline = "最近完整交易日已收在原賣壓區之上，但單日突破仍需後續價格與量能確認，不把一次收盤越過直接當成低風險買點。"
            buy_plan = f"若未持有，觀察後續是否站穩 {resistance['label']} 上緣，或回測原賣壓區不破後再分批評估。"
        else:
            action_state = "盤中突破，等待收盤確認"
            headline = "價格已越過原賣壓區，但盤中越過不等於有效突破；先確認收盤與量能，不把突破中的價格直接當成低風險買點。"
            buy_plan = f"若未持有，等待收盤站穩 {resistance['label']} 上緣，或後續回測原賣壓區不破再分批評估。"
    elif status == "偏多但不追價" or near_resistance:
        action_state = "不追價，等待較佳位置"
        headline = "現在不適合追價；較合理的是等回測支撐守穩，或等賣壓區被有效突破後再評估。"
        buy_plan = f"若未持有，先等 {support['label']} 附近止穩，或收盤確認突破 {resistance['label']}，不要在賣壓前一次投入。"
    elif low_zone_assessment.get("batch_entry_eligible"):
        action_state = "低檔止跌，可條件式第一批"
        headline = str(low_zone_assessment.get("summary") or "低檔止跌條件已通過。")
        buy_plan = " ".join(
            str(low_zone_assessment.get(key) or "").strip()
            for key in (
                "first_batch_condition",
                "second_batch_condition",
                "final_batch_condition",
            )
            if str(low_zone_assessment.get(key) or "").strip()
        )
    elif (
        low_zone_assessment.get("available")
        and low_zone_assessment.get("rsi_strategy_applicable")
    ):
        action_state = "低檔尚未止跌，等待確認"
        headline = str(
            low_zone_assessment.get("summary")
            or "RSI 雖在低檔，但止跌證據尚未完整。"
        )
        buy_plan = str(
            low_zone_assessment.get("first_batch_condition")
            or "若未持有，先等待 RSI、支撐與價格結構同步止穩。"
        )
    elif status == "可觀察" and at_support:
        action_state = "可條件式分批"
        headline = "位置接近支撐，可考慮條件式分批，但不適合一次押滿。"
        buy_plan = f"若未持有，可把 {support['label']} 視為第一批觀察區；前提是收盤沒有跌破支撐下緣。"
    elif status == "可觀察":
        action_state = "可小比例分批觀察"
        headline = "可以列入分批觀察，但目前不是非買不可的位置。"
        buy_plan = f"若未持有，第一批宜小，保留資金等待 {support['label']} 回測，或等 {resistance['label']} 突破確認。"
    elif status == "中性" and at_support and not momentum_weak:
        action_state = "支撐區小比例試單"
        headline = "目前不是明確多頭，但在支撐區可用小比例試單，重點是先定義失效條件。"
        buy_plan = f"若未持有，只在 {support['label']} 守穩時分批；未確認前不要把反彈當成趨勢反轉。"
    elif status == "警戒" and at_support and momentum_improving:
        action_state = "止跌確認後小比例試單"
        headline = "雖然仍屬警戒，但若支撐守住且動能持續改善，可等待止跌確認後小比例分批，不必把下跌一律視為不能買。"
        buy_plan = f"若未持有，先看 {support['label']} 能否守住並出現連續改善；確認前不搶反彈。"
    elif status == "警戒":
        action_state = "暫緩新增部位"
        headline = "目前先不要急著新增部位；應等價格重新站回支撐、弱勢訊號停止惡化後再評估。"
        buy_plan = f"若未持有，至少等收盤重新站回 {support['label']}；未站回前不把價格下跌直接當成便宜。"
    else:
        action_state = "等待確認"
        headline = "目前條件不夠集中，先等待支撐或突破訊號確認，比立即進場更合理。"
        buy_plan = f"若未持有，等待 {support['label']} 守穩或 {resistance['label']} 突破，再決定是否分批。"

    if safety_entry_blocked:
        holder_plan = "若已有部位，先確認官方交易狀態、流動性與公司行動影響，再依原始風險上限處理；本系統不以不完整安全資料追加判斷。"
    elif below_support or status == "高風險觀察":
        holder_plan = f"若已持有，收盤未能站回 {support['label']} 時應優先降低曝險；重新站回後再判斷是否保留核心部位。"
    elif above_resistance:
        holder_plan = f"若已持有，可保留核心部位觀察收盤能否站穩 {resistance['label']} 上緣；若重新跌回區間內，再評估分批調節。"
    elif near_resistance:
        holder_plan = f"若已持有，{resistance['label']} 無法突破時可分批調節；有效突破則保留核心部位並以原支撐作防守。"
    else:
        holder_plan = f"若已持有，可續抱觀察，但收盤跌破 {support['label']} 下緣時應降低部位；接近 {resistance['label']} 則觀察是否出現突破。"

    invalidation = (
        "安全資格尚未通過；待官方交易限制、流動性與公司行動狀態更新後必須重新評估。"
        if safety_entry_blocked
        else f"這套判斷在收盤跌破支撐下緣 {_format_price(support_low)} 時失效；突破賣壓上緣 {_format_price(resistance_high)} 後則需重新評估。"
    )
    evidence = [str(item) for item in (referee.get("main_reasons") or []) if str(item).strip()][:2]
    position_label = "盤中現價" if price_basis == "intraday" else "最近完整收盤價"
    evidence.append(f"{position_label}相對位置：支撐 {support['label']}／賣壓 {resistance['label']}")

    background_notes: list[str] = []
    event_context = official_event_context or {}
    if event_context.get("available") and str(event_context.get("note") or "").strip():
        background_notes.append(str(event_context["note"]))
    external_context = external_event_context or {}
    if external_context.get("available") and str(external_context.get("note") or "").strip():
        background_notes.append(str(external_context["note"]))
    night_context = taifex_night_context or {}
    if night_context.get("available") and str(night_context.get("note") or "").strip():
        background_notes.append(str(night_context["note"]))
    global_context = global_market_context or {}
    if global_context.get("available") and str(global_context.get("note") or "").strip():
        background_notes.append(str(global_context["note"]))
    institution = institutional_context or {}
    if institution.get("available") and str(institution.get("note") or "").strip():
        background_notes.append(str(institution["note"]))

    return {
        **base,
        "action_state": action_state,
        "headline": headline,
        "buy_plan": buy_plan,
        "holder_plan": holder_plan,
        "invalidation": invalidation,
        "evidence": evidence,
        "background_notes": background_notes[:3],
        "momentum_improving": momentum_improving,
        "low_zone_assessment": low_zone_assessment,
        "price_position": (
            "above_resistance"
            if above_resistance
            else "near_resistance"
            if near_resistance
            else "below_support"
            if below_support
            else "at_support"
            if at_support
            else "between_zones"
        ),
        "personalization_required": True,
        "personalization_question": "你是未持有、已有部位，還是想加碼？投資週期偏短線、波段或長期？",
    }
