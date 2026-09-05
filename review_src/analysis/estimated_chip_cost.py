from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable

from core.cost_source_registry import (
    CANONICAL_COST_CONTRACT_VERSION,
    CANONICAL_COST_FORMULA_VERSION,
    CANONICAL_COST_MIN_OFFICIAL_SESSIONS,
    CANONICAL_COST_TYPES,
    get_cost_definition,
    missing_fields_text,
)


FORMULA_VERSION = CANONICAL_COST_FORMULA_VERSION
RECENT_WINDOW_DAYS = 5
DISTRIBUTION_SELL_DAYS = 3
BUILDING_DEVIATION_PCT = 5.0
MARKUP_DEVIATION_PCT = 5.0


@dataclass(frozen=True)
class DailyCostInput:
    code: str
    trade_date: str
    close: float | None
    volume: float | None
    amount: float | None
    foreign_net: float | None = None
    trust_net: float | None = None
    institution_source: str | None = None
    institution_source_quality: str | None = None
    margin_balance: float | None = None
    margin_balance_unit: str | None = None
    foreign_holding_shares: float | None = None
    pv_weighted_cost: float | None = None
    pv_main_peak_price: float | None = None
    pv_quality: str | None = None
    pv_status: str | None = None
    pv_coverage_days: int | None = None
    pv_required_days: int | None = None


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.strip().replace(",", "")
            if not value:
                return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def safe_int(value: Any) -> int | None:
    number = safe_float(value)
    if number is None:
        return None
    return int(number)


def is_positive(value: Any) -> bool:
    number = safe_float(value)
    return number is not None and number > 0


def price_basis_for(row: DailyCostInput) -> tuple[str | None, float | None]:
    close = safe_float(row.close)
    volume = safe_float(row.volume)
    amount = safe_float(row.amount)
    if amount is not None and volume is not None and amount > 0 and volume > 0:
        vwap = amount / volume
        if close is None or (vwap > 0 and close * 0.2 <= vwap <= close * 5):
            return "vwap_from_amount_volume", vwap
    if close is not None and close > 0:
        return "close", close
    return None, None


def _row_base(
    row: DailyCostInput,
    cost_type: str,
    price_basis: str | None,
    price_value: float | None,
) -> dict[str, Any]:
    definition = get_cost_definition(cost_type)
    return {
        "code": row.code,
        "trade_date": row.trade_date,
        "cost_type": cost_type,
        "cost_label": definition.display_label,
        "estimated_cost": None,
        "cost_status": "unavailable",
        "confidence": "unavailable",
        "data_source_confidence": "unavailable",
        "data_source_status": None,
        "source_license": definition.source_license,
        "source_detail": definition.source_detail,
        "source_tables": ",".join(definition.source_tables),
        "calculation_method": definition.calculation_method,
        "formula_version": FORMULA_VERSION,
        "price_basis": price_basis,
        "price_basis_value": _round(price_value),
        "price_to_cost_deviation_pct": None,
        "accumulation_status": "unavailable",
        "position_shares": None,
        "total_cost_amount": None,
        "cumulative_net_shares": None,
        "estimate_start_date": None,
        "estimate_end_date": row.trade_date,
        "sample_days": None,
        "display_reason": None,
        "debug_reason": None,
        "missing_required_fields": None,
    }


def _round(value: Any, digits: int = 4) -> float | None:
    number = safe_float(value)
    if number is None:
        return None
    return round(number, digits)


def _deviation_pct(price: float | None, cost: float | None) -> float | None:
    if not is_positive(price) or not is_positive(cost):
        return None
    return (float(price) - float(cost)) / float(cost) * 100.0


def _accumulation_status(
    cost_status: str,
    deviation_pct: float | None,
    recent_net_values: Iterable[float],
    position_shares: float | None,
) -> str:
    if cost_status not in {"ok", "estimated", "proxy_only"}:
        return "unavailable"
    if deviation_pct is None:
        return "neutral"
    recent = [safe_float(v) or 0.0 for v in recent_net_values]
    recent_sum = sum(recent[-RECENT_WINDOW_DAYS:])
    recent_sells = sum(1 for v in recent[-DISTRIBUTION_SELL_DAYS:] if v < 0)
    position = safe_float(position_shares) or 0.0
    if deviation_pct > MARKUP_DEVIATION_PCT and recent_sells >= 2:
        return "distribution"
    if recent_sum > 0 and -BUILDING_DEVIATION_PCT <= deviation_pct <= BUILDING_DEVIATION_PCT:
        return "building"
    if recent_sum > 0 and deviation_pct > MARKUP_DEVIATION_PCT:
        return "markup"
    if position <= 0:
        return "neutral"
    return "neutral"


def _invalid_cost_row(row: DailyCostInput, cost_type: str, reason: str) -> dict[str, Any]:
    price_basis, price_value = price_basis_for(row)
    out = _row_base(row, cost_type, price_basis, price_value)
    out.update(
        {
            "cost_status": "invalid",
            "debug_reason": reason,
            "display_reason": "價格或成交量資料異常，該日不納入成本估算。",
            "sample_days": 0,
        }
    )
    return out


def calculate_institution_estimated_cost(
    inputs: list[DailyCostInput],
    cost_type: str,
) -> list[dict[str, Any]]:
    if cost_type not in {"foreign_estimated", "trust_estimated"}:
        raise ValueError(f"unsupported institution cost type: {cost_type}")
    rows = sorted(inputs, key=lambda r: (r.code, r.trade_date))
    results: list[dict[str, Any]] = []
    current_code: str | None = None
    position = 0.0
    total_cost = 0.0
    cumulative_net = 0.0
    start_date: str | None = None
    sample_days = 0
    recent_net: deque[float] = deque(maxlen=RECENT_WINDOW_DAYS)

    def reset_active_segment() -> None:
        nonlocal position, total_cost, cumulative_net, start_date, sample_days
        position = 0.0
        total_cost = 0.0
        cumulative_net = 0.0
        start_date = None
        sample_days = 0
        recent_net.clear()

    for row in rows:
        if row.code != current_code:
            current_code = row.code
            reset_active_segment()

        price_basis, price_value = price_basis_for(row)
        if not is_positive(price_value):
            reset_active_segment()
            results.append(_invalid_cost_row(row, cost_type, "invalid_price_or_volume"))
            continue

        net = safe_float(row.foreign_net if cost_type == "foreign_estimated" else row.trust_net)
        source_quality = str(row.institution_source_quality or "").strip().lower()
        if source_quality != "official":
            reset_active_segment()
            out = _row_base(row, cost_type, price_basis, price_value)
            out.update(
                {
                    "cost_status": "unavailable",
                    "display_reason": "該日缺少官方法人資料，近期增量部位推估已中斷並重新起算。",
                    "debug_reason": "non_official_or_unknown_institution_source",
                    "data_source_status": f"source_quality={source_quality or 'unknown'}",
                    "missing_required_fields": "official_institution_net",
                    "sample_days": 0,
                }
            )
            results.append(out)
            continue
        if net is None:
            reset_active_segment()
            out = _row_base(row, cost_type, price_basis, price_value)
            out.update(
                {
                    "cost_status": "unavailable",
                    "display_reason": "缺少官方法人買賣超資料，近期增量部位推估已中斷並重新起算。",
                    "debug_reason": "missing_official_institution_net",
                    "data_source_status": "official_only;gap_reset=true",
                    "missing_required_fields": "foreign_net" if cost_type == "foreign_estimated" else "trust_net",
                    "sample_days": 0,
                }
            )
            results.append(out)
            continue

        if position <= 0 and net <= 0:
            reset_active_segment()
            out = _row_base(row, cost_type, price_basis, price_value)
            out.update(
                {
                    "cost_status": "insufficient_data",
                    "display_reason": "目前沒有可延續的近期增量買進部位，暫不產生成本數字。",
                    "debug_reason": "non_positive_estimated_position",
                    "data_source_confidence": "high",
                    "data_source_status": "official_only;continuous_sessions=0",
                    "position_shares": 0.0,
                    "total_cost_amount": 0.0,
                    "cumulative_net_shares": 0.0,
                    "sample_days": 0,
                }
            )
            results.append(out)
            continue

        if position <= 0:
            reset_active_segment()
            start_date = row.trade_date
        sample_days += 1
        cumulative_net += net
        recent_net.append(net)

        if net > 0:
            total_cost += net * float(price_value)
            position += net
            start_date = start_date or row.trade_date
        elif net < 0 and position > 0:
            avg_cost = total_cost / position if position > 0 else 0.0
            sell_shares = min(abs(net), position)
            total_cost -= sell_shares * avg_cost
            position -= sell_shares
            if position <= 0:
                reset_active_segment()

        estimated_cost = total_cost / position if position > 0 and total_cost > 0 else None
        out = _row_base(row, cost_type, price_basis, price_value)
        if estimated_cost is None:
            out.update(
                {
                    "cost_status": "insufficient_data",
                    "display_reason": "目前估算部位不足，暫不產生成本數字。",
                    "debug_reason": "non_positive_estimated_position",
                    "data_source_confidence": "high",
                    "data_source_status": "official_only;continuous_sessions=0",
                    "position_shares": _round(position),
                    "total_cost_amount": _round(total_cost),
                    "cumulative_net_shares": _round(cumulative_net),
                    "estimate_start_date": start_date,
                    "sample_days": sample_days,
                }
            )
        else:
            confidence = "medium" if sample_days >= CANONICAL_COST_MIN_OFFICIAL_SESSIONS else "low"
            deviation = _deviation_pct(price_value, estimated_cost)
            out.update(
                {
                    "estimated_cost": _round(estimated_cost),
                    "cost_status": "estimated",
                    "confidence": confidence,
                    "data_source_confidence": "high",
                    "data_source_status": f"official_only;continuous_sessions={sample_days}",
                    "price_to_cost_deviation_pct": _round(deviation),
                    "accumulation_status": _accumulation_status("estimated", deviation, recent_net, position),
                    "position_shares": _round(position),
                    "total_cost_amount": _round(total_cost),
                    "cumulative_net_shares": _round(cumulative_net),
                    "estimate_start_date": start_date,
                    "sample_days": sample_days,
                    "display_reason": (
                        "以官方法人淨買賣股數與當日官方成交均價推估近期增量部位，並非全部真實持倉成本。"
                        if confidence == "medium"
                        else f"官方連續資料僅 {sample_days} 個交易日，未達 {CANONICAL_COST_MIN_OFFICIAL_SESSIONS} 日，數字不對一般使用者顯示。"
                    ),
                    "debug_reason": "official_net_flow_incremental_inventory",
                }
            )
        results.append(out)
    return results


def build_canonical_cost_snapshot(
    rows: Iterable[dict[str, Any]],
    expected_trade_date: str | None,
) -> dict[str, Any]:
    """Project persisted rows into the only cost contract used by web and LINE."""

    expected = str(expected_trade_date or "").strip() or None
    by_type = {
        str(row.get("cost_type") or ""): dict(row)
        for row in rows
        if str(row.get("formula_version") or "") == FORMULA_VERSION
        and str(row.get("trade_date") or "") == str(expected or "")
        and str(row.get("cost_type") or "") in CANONICAL_COST_TYPES
    }
    metric_ids = {
        "foreign_estimated": "foreign_incremental_position_avg_price_estimate",
        "trust_estimated": "trust_incremental_position_avg_price_estimate",
    }
    items: dict[str, dict[str, Any]] = {}
    for cost_type in CANONICAL_COST_TYPES:
        definition = get_cost_definition(cost_type)
        raw = by_type.get(cost_type) or {}
        sample_days = safe_int(raw.get("sample_days")) or 0
        raw_value = safe_float(raw.get("estimated_cost"))
        source_mode = str(raw.get("data_source_status") or "")
        stored_status = str(raw.get("cost_status") or "unavailable").lower()
        ready = bool(
            raw
            and raw_value is not None
            and raw_value > 0
            and stored_status == "estimated"
            and sample_days >= CANONICAL_COST_MIN_OFFICIAL_SESSIONS
            and source_mode.startswith("official_only;")
        )
        if ready:
            calculation_state = "ready"
            confidence = "medium"
            reason_codes: list[str] = []
            note = "官方盤後資料的近期增量部位均價推估；只作背景資訊，不代表真實總持倉成本。"
        elif raw and stored_status == "estimated" and sample_days < CANONICAL_COST_MIN_OFFICIAL_SESSIONS:
            calculation_state = "insufficient_history"
            confidence = "low"
            reason_codes = ["official_history_below_60_sessions"]
            note = f"官方連續資料僅 {sample_days} 個交易日，未達 {CANONICAL_COST_MIN_OFFICIAL_SESSIONS} 日，暫不顯示成本數字。"
        elif raw:
            calculation_state = stored_status
            confidence = "unavailable"
            reason_codes = [str(raw.get("debug_reason") or stored_status)]
            note = str(raw.get("display_reason") or "目前沒有可用的官方近期增量成本推估。")
        else:
            calculation_state = "missing"
            confidence = "unavailable"
            reason_codes = ["canonical_row_missing_for_trade_date"]
            note = "指定完整交易日尚無同版公式的成本資料。"
        item = {
            "contract_version": CANONICAL_COST_CONTRACT_VERSION,
            "formula_version": FORMULA_VERSION,
            "metric_id": metric_ids[cost_type],
            "cost_type": cost_type,
            "label": definition.display_label,
            "value": _round(raw_value) if ready else None,
            "estimated_cost": _round(raw_value) if ready else None,
            "cost_label": definition.display_label,
            "unit": "TWD_per_share",
            "available": ready,
            "trade_date": expected,
            "as_of_trade_date": expected,
            "quality_state": "estimated" if ready else "unavailable",
            "status": "estimated" if ready else calculation_state,
            "calculation_state": calculation_state,
            "confidence": confidence,
            "source_confidence": "high" if raw and source_mode.startswith("official_only;") else "unavailable",
            "source_mode": "official_only",
            "sample_days": sample_days,
            "required_days": CANONICAL_COST_MIN_OFFICIAL_SESSIONS,
            "is_estimated": True,
            "is_total_holding_cost": False,
            "referee_eligible": False,
            "support_resistance_eligible": False,
            "next_day_outlook_eligible": False,
            "score_weight": 0,
            "can_override_main_status": False,
            "note": note,
            "display_reason": note,
            "reason_codes": reason_codes,
            "extra": {
                "estimate_start_date": raw.get("estimate_start_date"),
                "estimate_end_date": raw.get("estimate_end_date"),
            },
        }
        items[cost_type] = item

    available_items = [item for item in items.values() if item["available"]]
    return {
        "contract_version": CANONICAL_COST_CONTRACT_VERSION,
        "formula_version": FORMULA_VERSION,
        "trade_date": expected,
        "status": "ok" if available_items else "unavailable",
        "items": items,
        "costs": items,
        "estimated_costs": available_items,
        "can_override_main_status": False,
        "foreign_cost_estimate": items["foreign_estimated"],
        "trust_buy_cost_estimate": items["trust_estimated"],
        "main_force_branch_cost": {
            "label": "指定分點群近期淨部位均價推估",
            "value": None,
            "available": False,
            "calculation_state": "missing_required_source",
            "confidence": "unavailable",
            "reason_codes": ["missing_authorized_branch_trades"],
            "referee_eligible": False,
            "support_resistance_eligible": False,
            "can_override_main_status": False,
            "note": "尚無完整且授權明確的券商分點量額資料，不以成交密集價或法人資料代替。",
        },
        "input_issues": [
            item["note"] for item in items.values() if not item["available"]
        ],
        "disclaimer": "近期增量部位均價為公開盤後資料推估，不是真實總持倉成本，且不納入主判斷。",
    }


def calculate_margin_incremental_estimated_cost(inputs: list[DailyCostInput]) -> list[dict[str, Any]]:
    rows = sorted(inputs, key=lambda r: (r.code, r.trade_date))
    results: list[dict[str, Any]] = []
    current_code: str | None = None
    previous_balance: float | None = None
    position = 0.0
    total_cost = 0.0
    cumulative_net = 0.0
    sample_days = 0
    start_date: str | None = None
    recent_net: deque[float] = deque(maxlen=RECENT_WINDOW_DAYS)

    for row in rows:
        if row.code != current_code:
            current_code = row.code
            previous_balance = None
            position = 0.0
            total_cost = 0.0
            cumulative_net = 0.0
            sample_days = 0
            start_date = None
            recent_net.clear()

        price_basis, price_value = price_basis_for(row)
        out = _row_base(row, "margin_incremental_estimated", price_basis, price_value)
        unit = (row.margin_balance_unit or "unit_unknown").strip() or "unit_unknown"
        balance = safe_float(row.margin_balance)

        if unit == "unit_unknown":
            out.update(
                {
                    "cost_status": "unavailable",
                    "confidence": "unavailable",
                    "data_source_confidence": "unavailable",
                    "data_source_status": "unit_unknown",
                    "display_reason": "融資餘額單位尚未確認，暫不估算融資新增部位成本。",
                    "debug_reason": "unit_unknown",
                    "missing_required_fields": "margin_balance_unit_verified",
                    "sample_days": sample_days,
                }
            )
            previous_balance = balance if balance is not None else previous_balance
            results.append(out)
            continue

        if balance is None or not is_positive(price_value):
            out.update(
                {
                    "cost_status": "invalid",
                    "display_reason": "融資餘額或價格資料異常，該日不納入估算。",
                    "debug_reason": "invalid_margin_balance_or_price",
                    "sample_days": sample_days,
                }
            )
            results.append(out)
            continue

        delta = 0.0 if previous_balance is None else balance - previous_balance
        previous_balance = balance
        sample_days += 1
        cumulative_net += delta
        recent_net.append(delta)

        if delta > 0:
            shares = delta * 1000.0 if unit in {"lots", "lot"} else delta
            total_cost += shares * float(price_value)
            position += shares
            start_date = start_date or row.trade_date
        elif delta < 0 and position > 0:
            shares = abs(delta * 1000.0 if unit in {"lots", "lot"} else delta)
            avg_cost = total_cost / position if position > 0 else 0.0
            reduce_shares = min(shares, position)
            total_cost -= reduce_shares * avg_cost
            position -= reduce_shares
            if position <= 0:
                position = 0.0
                total_cost = 0.0

        estimated_cost = total_cost / position if position > 0 and total_cost > 0 else None
        if estimated_cost is None:
            out.update(
                {
                    "cost_status": "insufficient_data",
                    "display_reason": "融資新增部位不足，暫不產生成本數字。",
                    "debug_reason": "non_positive_estimated_position",
                    "sample_days": sample_days,
                    "position_shares": _round(position),
                    "total_cost_amount": _round(total_cost),
                    "cumulative_net_shares": _round(cumulative_net),
                    "estimate_start_date": start_date,
                }
            )
        else:
            deviation = _deviation_pct(price_value, estimated_cost)
            out.update(
                {
                    "estimated_cost": _round(estimated_cost),
                    "cost_status": "estimated",
                    "confidence": "medium",
                    "data_source_confidence": "medium",
                    "data_source_status": f"margin_balance_unit={unit}",
                    "price_to_cost_deviation_pct": _round(deviation),
                    "accumulation_status": _accumulation_status("estimated", deviation, recent_net, position),
                    "position_shares": _round(position),
                    "total_cost_amount": _round(total_cost),
                    "cumulative_net_shares": _round(cumulative_net),
                    "estimate_start_date": start_date,
                    "sample_days": sample_days,
                    "display_reason": "以融資餘額變化推估新增部位成本，非可靠融資放款均額。",
                    "debug_reason": "margin_balance_delta_estimate",
                }
            )
        results.append(out)
    return results


def missing_required_source_rows(inputs: list[DailyCostInput], cost_type: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    missing = missing_fields_text(cost_type)
    for row in sorted(inputs, key=lambda r: (r.code, r.trade_date)):
        price_basis, price_value = price_basis_for(row)
        out = _row_base(row, cost_type, price_basis, price_value)
        out.update(
            {
                "cost_status": "missing_required_source",
                "display_reason": "缺少授權來源必要欄位，暫不計算此類成本。",
                "debug_reason": "missing_required_source",
                "missing_required_fields": missing,
                "sample_days": 0,
            }
        )
        rows.append(out)
    return rows


def calculate_main_force_reference_zone(inputs: list[DailyCostInput]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in sorted(inputs, key=lambda r: (r.code, r.trade_date)):
        price_basis, price_value = price_basis_for(row)
        out = _row_base(row, "main_force_reference_zone", price_basis, price_value)
        reference = safe_float(row.pv_weighted_cost) or safe_float(row.pv_main_peak_price)
        status = (row.pv_status or "").lower()
        quality = (row.pv_quality or "").lower()
        if not is_positive(reference) or status not in {"ok", "low_confidence", "partial"}:
            out.update(
                {
                    "cost_status": "unavailable",
                    "display_reason": "缺少可用成交密集區資料，暫不能計算主力參考成本區。",
                    "debug_reason": "no_valid_reference_zone_source",
                    "missing_required_fields": "existing_poc_or_price_volume_profile",
                    "sample_days": 0,
                }
            )
        else:
            deviation = _deviation_pct(price_value, reference)
            confidence = "medium" if quality == "ok" and (row.pv_coverage_days or 0) >= max(1, int((row.pv_required_days or 1) * 0.8)) else "low"
            out.update(
                {
                    "estimated_cost": _round(reference),
                    "cost_status": "proxy_only",
                    "confidence": confidence,
                    "data_source_confidence": confidence,
                    "data_source_status": f"price_volume_status={status};quality={quality}",
                    "price_to_cost_deviation_pct": _round(deviation),
                    "accumulation_status": _accumulation_status("proxy_only", deviation, [], None),
                    "sample_days": safe_int(row.pv_coverage_days) or 0,
                    "display_reason": "以市場成交密集區作為主力參考成本區，非券商分點成本。",
                    "debug_reason": "existing_price_volume_proxy",
                }
            )
        rows.append(out)
    return rows


def build_estimated_chip_cost_rows(inputs: list[DailyCostInput]) -> list[dict[str, Any]]:
    return (
        calculate_institution_estimated_cost(inputs, "foreign_estimated")
        + calculate_institution_estimated_cost(inputs, "trust_estimated")
        + calculate_margin_incremental_estimated_cost(inputs)
        + missing_required_source_rows(inputs, "margin_reliable_cost")
        + calculate_main_force_reference_zone(inputs)
        + missing_required_source_rows(inputs, "main_force_branch_cost")
    )
