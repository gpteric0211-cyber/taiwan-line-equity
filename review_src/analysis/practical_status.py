from __future__ import annotations

import math
from typing import Any

from core.utils import parse_num


PRACTICAL_STATUS_CORE_VERSION = "dashboard-practical-status-core-v1"


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def classify_practical_status_core(data: dict[str, Any]) -> dict[str, Any]:
    """Pure, shared referee core used by the dashboard and read-only Bot."""

    def unavailable() -> dict[str, Any]:
        return {"status": "資料不足", "reasons": []}

    try:
        positive_required = [
            "current_price",
            "previous_close",
            "ma20",
            "ma20_3days_ago",
            "ma60",
            "atr",
            "volume_avg_20d",
            "low_10d",
            "support_zone_upper",
            "support_zone_lower",
            "resistance_zone_upper",
        ]
        numeric_required = ["rsi", "macd_osc", "macd_osc_prev", "volume"]
        values: dict[str, float] = {}
        for key in positive_required + numeric_required:
            value = _finite_float(data.get(key))
            if value is None:
                return unavailable()
            values[key] = value
        for key in positive_required:
            if values[key] <= 0:
                return unavailable()
        if values["volume"] < 0 or not 0 <= values["rsi"] <= 100:
            return unavailable()
        if values["support_zone_lower"] > values["support_zone_upper"]:
            return unavailable()

        current_price = values["current_price"]
        previous_close = values["previous_close"]
        ma20 = values["ma20"]
        ma20_3days_ago = values["ma20_3days_ago"]
        ma60 = values["ma60"]
        rsi = values["rsi"]
        macd_osc = values["macd_osc"]
        macd_osc_prev = values["macd_osc_prev"]
        atr = values["atr"]
        volume = values["volume"]
        volume_avg_20d = values["volume_avg_20d"]
        low_10d = values["low_10d"]
        support_zone_upper = values["support_zone_upper"]
        support_zone_lower = values["support_zone_lower"]
        resistance_zone_upper = values["resistance_zone_upper"]

        ma20_slope_pct = ((ma20 - ma20_3days_ago) / ma20_3days_ago) * 100
        volume_ratio = volume / volume_avg_20d
        candidates = [support_zone_upper, ma20, low_10d, ma20 - (1.5 * atr)]
        valid_candidates = [
            candidate
            for candidate in candidates
            if candidate > 0 and math.isfinite(candidate) and candidate <= current_price
        ]
        if not valid_candidates:
            return unavailable()
        ref_price = min(valid_candidates, key=lambda candidate: current_price - candidate)
        distance_pct = ((current_price - ref_price) / current_price) * 100
        distance_atr = (current_price - ref_price) / atr

        alerts: list[dict[str, str]] = []
        if current_price < ma20 and ma20_slope_pct < -0.3:
            alerts.append({"type": "trend", "text": "股價跌破月線，短期買氣轉弱"})

        critical_risk_distance = distance_pct > 8 and distance_atr > 2.5
        if distance_pct > 5:
            alerts.append(
                {
                    "type": "distance",
                    "text": f"現價離下方支撐較遠（約 {distance_pct:.1f}%），追價風險偏高",
                }
            )

        if rsi > 75 and macd_osc < macd_osc_prev and volume_ratio < 1.0:
            alerts.append({"type": "rsi", "text": "短線過熱，量價轉弱"})
        if 70 <= rsi <= 75:
            alerts.append({"type": "rsi", "text": "短線技術指標偏高，建議等拉回"})
        if 45 <= rsi < 50:
            alerts.append({"type": "rsi", "text": "RSI偏弱"})
        if rsi < 45:
            alerts.append({"type": "rsi", "text": "RSI轉弱"})
        if macd_osc < 0 and macd_osc < macd_osc_prev:
            alerts.append({"type": "momentum", "text": "上漲力道轉弱"})

        is_valid_breakout = (
            previous_close <= resistance_zone_upper
            and current_price > resistance_zone_upper
            and volume_ratio >= 1.8
        )
        is_structural_positive = (
            current_price >= ma20 and ma20 >= ma60 and ma20_slope_pct > 0
        )
        is_oversold_bounce = (
            rsi < 25
            and volume < (volume_avg_20d * 0.5)
            and current_price >= (support_zone_lower * 0.98)
        )

        filtered_alerts = (
            [alert for alert in alerts if alert.get("type") not in {"distance", "rsi"}]
            if is_valid_breakout
            else alerts
        )

        if volume_ratio >= 1.5 and current_price < ma20 and current_price < low_10d:
            return {
                "status": "高風險觀察",
                "reasons": ["股價爆量跌破前低與月線，結構明顯轉弱，承接風險升高"],
            }
        if critical_risk_distance:
            return {
                "status": "高風險觀察",
                "reasons": [
                    f"現價與下方支撐區已拉開較大落差（約 {distance_pct:.1f}%），短線回檔空間偏大"
                ],
            }
        if is_valid_breakout:
            reasons = ["爆量突破賣壓，攻擊動能增強"]
            reasons.append("穩守月線支撐之上" if current_price >= ma20 else "仍需觀察能否站回月線")
            return {"status": "可觀察", "reasons": reasons[:2]}
        if len(filtered_alerts) >= 2:
            priority = {"distance": 0, "trend": 1, "momentum": 2, "rsi": 3}
            sorted_alerts = sorted(
                filtered_alerts,
                key=lambda alert: priority.get(alert.get("type"), 99),
            )
            return {
                "status": "警戒",
                "reasons": [alert["text"] for alert in sorted_alerts[:2]],
            }
        if is_structural_positive and (rsi >= 70 or distance_pct > 5) and volume_ratio < 1.2:
            return {
                "status": "偏多但不追價",
                "reasons": [
                    "短線技術指標偏高，建議等拉回",
                    f"目前潛在回檔空間約 {distance_pct:.1f}%",
                ],
            }
        if (
            current_price < ma20
            and ma20_slope_pct > 0.3
            and (
                abs(current_price - ma20) / ma20 <= 0.02
                or support_zone_lower <= current_price <= support_zone_upper * 1.02
            )
        ):
            return {"status": "可觀察", "reasons": ["回測月線支撐，中期趨勢仍具支撐力"]}
        if is_oversold_bounce:
            return {"status": "可觀察", "reasons": ["短線超跌，留意反彈"]}
        if (
            current_price >= ma20
            and ma20 >= ma60
            and ma20_slope_pct >= 0
            and not filtered_alerts
        ):
            return {"status": "可觀察", "reasons": ["均線結構偏多，走勢維持穩定"]}
        return {"status": "中性", "reasons": ["指標多空交錯，結構進入區間震盪"]}
    except Exception:
        return {"status": "資料不足", "reasons": []}


def _practical_status_badge(status: str) -> tuple[int, str]:
    if status == "可觀察":
        return 1, "positive"
    if status == "偏多但不追價":
        return 2, "hot"
    if status == "警戒":
        return 3, "warning"
    if status == "高風險觀察":
        return 4, "danger"
    if status == "資料不足":
        return 5, "muted"
    return 3, "muted"


def has_severe_warning(warn_reasons: list[str]) -> bool:
    severe_keywords = [
        '放量跌破支撐', '已跌破支撐', '跌破MA20', 'RSI偏弱', '跳空高開收黑',
        '突破後收弱', '無量突破賣壓', '融資增加但股價偏弱', '跌破支撐且量能偏大',
        'RSI過熱且量價轉弱',
    ]
    for reason in warn_reasons or []:
        text = str(reason)
        if any(k in text for k in severe_keywords):
            return True
    return False


def calc_risk_reward_ratio(close: float | None, stop_loss: float | None, resistance_zone: dict[str, Any] | None, resistance_pos: dict[str, Any]) -> tuple[float | None, str | None]:
    # Estimate risk/reward from nearest resistance and selected stop-loss candidate.
    if not close or not stop_loss or stop_loss >= close:
        return None, None
    if resistance_pos and resistance_pos.get('state') == 'broken_up':
        return None, '已突破原賣壓區，原壓力不再作為RR目標，需用新高/ATR重新估算'
    target = None
    if resistance_zone:
        target = parse_num(resistance_zone.get('zone_low')) or parse_num(resistance_zone.get('price'))
    if not target or target <= close:
        return None, '上方有效賣壓/目標不足'
    downside = close - stop_loss
    upside = target - close
    if downside <= 0 or upside <= 0:
        return None, None
    return upside / downside, None

