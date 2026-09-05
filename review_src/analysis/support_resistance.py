from __future__ import annotations

import sqlite3
import math
from typing import Any, Callable

from analysis.technical import calc_ma_from_rows, calc_pivot_from_rows
from core.utils import fmt, parse_num


SUPPORT_RESISTANCE_ASSEMBLER_VERSION = "dashboard-ohlcv-support-resistance-v2"

def price_bin_size(price: float) -> float:
    """Dynamic VP bin size. Keep high-price stocks from using overly coarse 10-dollar bins."""
    price = float(price or 0)
    if price <= 0:
        return 1.0
    raw = price * 0.005  # about 0.5%
    if raw < 0.1:
        return 0.1
    if raw < 0.5:
        return 0.5
    if raw < 1.0:
        return 1.0
    if raw < 2.5:
        return 2.5
    return 5.0


def round_to_bin(price: float, bin_size: float) -> float:
    return round(round(price / bin_size) * bin_size, 4)


def basis_price(price: float | None, eod: sqlite3.Row | None, hist: list[sqlite3.Row]) -> float | None:
    """Best available numeric basis price for display fallbacks."""
    p = parse_num(price)
    if p is not None:
        return p
    if eod and eod["close"] is not None:
        return parse_num(eod["close"])
    for r in hist:
        p = parse_num(r["close"])
        if p is not None:
            return p
    return None


def neutral_rsi_text(rsi5: float | None, rsi10: float | None) -> str:
    if rsi5 is not None and rsi10 is not None:
        return f"{fmt(rsi5)} / {fmt(rsi10)}"
    return "資料缺口"


def fallback_support_resistance_text(price: float | None, hist: list[sqlite3.Row]) -> str:
    p = basis_price(price, None, hist)
    lows = [parse_num(r["low"]) for r in hist if parse_num(r["low"]) is not None]
    highs = [parse_num(r["high"]) for r in hist if parse_num(r["high"]) is not None]
    if lows and highs:
        return f"支撐 {fmt(min(lows))} 弱｜賣壓 {fmt(max(highs))} 弱｜短期K棒估算"
    if p is not None:
        return f"支撐 {fmt(p * 0.97)} 弱｜賣壓 {fmt(p * 1.03)} 弱｜現價估算"
    return "支撐 N/A｜賣壓 N/A｜尚缺價格"


def level_break_state(level: float, rows: list[dict[str, Any]], current: float) -> str:
    # State for old swing levels: active, reclaimed, broken.
    recent = rows[-5:] if len(rows) >= 5 else rows
    closes = [
        parse_num(r.get('technical_close'))
        if r.get('technical_close') is not None
        else parse_num(r.get('close'))
        for r in recent
    ]
    closes = [x for x in closes if x is not None]
    if not closes:
        return 'unknown'
    below_count = sum(1 for x in closes[-3:] if x < level)
    if current > level:
        return 'reclaimed' if below_count >= 2 else 'active'
    if current < level:
        return 'broken'
    return 'unknown'


def cluster_levels(levels: list[dict[str, Any]], current: float) -> list[dict[str, Any]]:
    if not levels:
        return []
    levels = [x for x in levels if abs(x['price'] - current) / current <= 0.20]
    levels.sort(key=lambda x: x['price'])
    clusters: list[list[dict[str, Any]]] = []
    tol = max(current * 0.01, price_bin_size(current) * 2)
    for lv in levels:
        if not clusters or abs(lv['price'] - clusters[-1][-1]['price']) > tol:
            clusters.append([lv])
        else:
            clusters[-1].append(lv)
    out = []
    for group in clusters:
        weight = sum(x['base_score'] for x in group) or 1
        representative = sum(x['price'] * x['base_score'] for x in group) / weight
        zone_low = min(x['price'] for x in group)
        zone_high = max(x['price'] for x in group)
        sources = []
        raw_levels = []
        for x in sorted(group, key=lambda z: z['base_score'], reverse=True):
            if x['source'] not in sources:
                sources.append(x['source'])
            raw_levels.append({'price': x['price'], 'source': x['source'], 'status': x.get('status', 'active'), 'score': x['base_score']})
        base = max(x['base_score'] for x in group)
        confluence = min(4.0, (len(sources) - 1) * 1.5)
        signed_distance_pct = (representative - current) / current * 100
        distance_pct = abs(signed_distance_pct)
        decay = max(0.18, 1 - max(0, distance_pct - 3) / 12)
        score = (base + confluence) * decay
        if len(sources) >= 3:
            score += 1.0
        strength = '強' if score >= 7 else ('中' if score >= 4 else '弱')
        label_price = f"{fmt(zone_low)}~{fmt(zone_high)}" if abs(zone_high - zone_low) >= 0.01 else fmt(representative)
        out.append({
            'price': round(representative, 2),
            'zone_low': round(zone_low, 2),
            'zone_high': round(zone_high, 2),
            'label_price': label_price,
            'sources': sources[:5],
            'raw_levels': raw_levels[:8],
            'score': round(score, 2),
            'strength': strength,
            'distance_pct': round(distance_pct, 2),
            'signed_distance_pct': round(signed_distance_pct, 2),
        })
    out.sort(key=lambda x: x['score'], reverse=True)
    return out


def _weighted_volume_profile(
    rows: list[dict[str, Any]],
    current: float,
    days: int,
    label: str,
) -> list[dict[str, Any]]:
    """Build the existing dashboard OHLCV-weighted price-density nodes."""

    use = rows[-days:] if len(rows) > days else rows
    if len(use) < max(5, min(days, 10)):
        return []
    bin_size = price_bin_size(current)
    volume_by_bin: dict[float, float] = {}
    total_volume = 0.0
    for row in use:
        high = parse_num(row.get("high"))
        low = parse_num(row.get("low"))
        close = parse_num(row.get("close"))
        open_price = parse_num(row.get("open"))
        volume = parse_num(row.get("volume"))
        if high is None or low is None or close is None or volume is None or volume <= 0:
            continue
        if high < low:
            high, low = low, high
        if high == low:
            price_bin = round_to_bin(close, bin_size)
            volume_by_bin[price_bin] = volume_by_bin.get(price_bin, 0.0) + volume
            total_volume += volume
            continue
        typical = (high + low + close) / 3.0
        mode_price = (
            typical
            if abs(close - typical) / typical > 0.03
            else (0.65 * close + 0.35 * typical)
        )
        sigma = max((high - low) / 4.0, bin_size * 2)
        start = round_to_bin(low, bin_size)
        end = round_to_bin(high, bin_size)
        prices: list[float] = []
        cursor = start
        guard = 0
        while cursor <= end + 1e-9 and guard < 500:
            prices.append(round(cursor, 4))
            cursor += bin_size
            guard += 1
        if not prices:
            prices = [round_to_bin(close, bin_size)]
        weights: list[float] = []
        for price in prices:
            weight = math.exp(-0.5 * ((price - mode_price) / sigma) ** 2)
            if open_price is not None and abs(price - open_price) <= bin_size:
                weight *= 1.08
            if abs(price - close) <= bin_size:
                weight *= 1.15
            weights.append(weight)
        weight_sum = sum(weights) or 1.0
        for price, weight in zip(prices, weights):
            volume_by_bin[price] = (
                volume_by_bin.get(price, 0.0) + volume * weight / weight_sum
            )
        total_volume += volume
    if not volume_by_bin or total_volume <= 0:
        return []
    raw = sorted(volume_by_bin.items(), key=lambda item: item[1], reverse=True)
    nodes: list[dict[str, Any]] = []
    for price, volume in raw:
        if any(abs(price - node["price"]) <= bin_size * 1.5 for node in nodes):
            continue
        nodes.append(
            {
                "price": round(price, 2),
                "source": f"{label}成交密集區",
                "volume_share": round(volume / total_volume * 100, 2),
                "base_score": 4.0 if days == 20 else (3.0 if days == 60 else 2.5),
            }
        )
        if len(nodes) >= 8:
            break
    return nodes


def _add_level(
    levels: list[dict[str, Any]],
    price: float | None,
    current: float,
    source: str,
    base_score: float,
    side: str | None = None,
    status: str = "active",
) -> None:
    if price is None or current is None or price <= 0:
        return
    if side is None:
        if price < current:
            side = "support"
        elif price > current:
            side = "resistance"
        else:
            return
    if side == "support" and price >= current:
        return
    if side == "resistance" and price <= current:
        return
    levels.append(
        {
            "price": round(float(price), 2),
            "source": source,
            "base_score": float(base_score),
            "side": side,
            "status": status,
        }
    )


def _add_swing_low(
    levels: list[dict[str, Any]],
    level: float | None,
    current: float,
    rows: list[dict[str, Any]],
    label: str,
    score: float,
) -> None:
    if level is None:
        return
    state = level_break_state(level, rows, current)
    if current > level:
        if state == "reclaimed":
            _add_level(
                levels,
                level,
                current,
                f"{label} 跌破後站回觀察",
                score * 0.65,
                "support",
                "reclaimed_support",
            )
        else:
            _add_level(
                levels, level, current, label, score, "support", "active_support"
            )
    elif current < level:
        _add_level(
            levels,
            level,
            current,
            f"{label} 失守轉壓",
            score,
            "resistance",
            "broken_to_resistance",
        )


def _add_swing_high(
    levels: list[dict[str, Any]],
    level: float | None,
    current: float,
    rows: list[dict[str, Any]],
    label: str,
    score: float,
) -> None:
    if level is None:
        return
    state = level_break_state(level, rows, current)
    if current < level:
        _add_level(
            levels, level, current, label, score, "resistance", "active_resistance"
        )
    elif current > level:
        if state == "reclaimed":
            _add_level(
                levels,
                level,
                current,
                f"{label} 突破後回測觀察",
                score * 0.75,
                "support",
                "breakout_retest",
            )
        else:
            _add_level(
                levels,
                level,
                current,
                f"{label} 突破轉支撐",
                score * 0.85,
                "support",
                "broken_resistance_to_support",
            )


def _volume_bar_type(row: dict[str, Any]) -> str:
    open_price = parse_num(row.get("open"))
    high = parse_num(row.get("high"))
    low = parse_num(row.get("low"))
    close = parse_num(row.get("close"))
    if (
        open_price is None
        or high is None
        or low is None
        or close is None
        or high <= low
    ):
        return "unknown"
    close_position = (close - low) / (high - low)
    if close > open_price and close_position >= 0.65:
        return "accumulation"
    if close < open_price and close_position <= 0.35:
        return "distribution"
    return "mixed"


def build_ohlcv_support_resistance_levels(
    rows: list[dict[str, Any]],
    current: float,
    *,
    enrich_levels: Callable[[list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, Any]]:
    """Shared dashboard/Bot level assembler; it does not read or mutate storage."""

    levels: list[dict[str, Any]] = []
    if len(rows) >= 20:
        for days, label in ((5, "5日"), (20, "20日"), (60, "60日")):
            if len(rows) < min(days, 20):
                continue
            for node in _weighted_volume_profile(rows, current, days, label):
                _add_level(
                    levels,
                    node["price"],
                    current,
                    f"{node['source']}({node['volume_share']}%)",
                    node["base_score"],
                    status="volume_profile",
                )

    if enrich_levels is not None:
        enrich_levels(levels)

    ma20 = calc_ma_from_rows(rows, 20)
    ma60 = calc_ma_from_rows(rows, 60)
    _add_level(levels, ma20, current, "MA20", 2.0)
    _add_level(levels, ma60, current, "MA60", 2.4)

    previous20 = rows[-21:-1] if len(rows) >= 21 else rows[:-1]
    previous60 = rows[-61:-1] if len(rows) >= 61 else rows[:-1]
    highs20 = [parse_num(row.get("high")) for row in previous20]
    lows20 = [parse_num(row.get("low")) for row in previous20]
    highs60 = [parse_num(row.get("high")) for row in previous60]
    lows60 = [parse_num(row.get("low")) for row in previous60]
    highs20 = [value for value in highs20 if value is not None]
    lows20 = [value for value in lows20 if value is not None]
    highs60 = [value for value in highs60 if value is not None]
    lows60 = [value for value in lows60 if value is not None]
    if lows20:
        _add_swing_low(levels, min(lows20), current, rows, "前20日低點", 3.0)
    if highs20:
        _add_swing_high(levels, max(highs20), current, rows, "前20日高點", 3.0)
    if lows60:
        _add_swing_low(levels, min(lows60), current, rows, "前60日低點", 2.5)
    if highs60:
        _add_swing_high(levels, max(highs60), current, rows, "前60日高點", 2.5)

    recent20 = rows[-20:] if len(rows) >= 20 else rows
    volume_rows = [(parse_num(row.get("volume")) or 0, row) for row in recent20]
    volume_rows.sort(key=lambda item: item[0], reverse=True)
    for _volume, row in volume_rows[:2]:
        low = parse_num(row.get("low"))
        high = parse_num(row.get("high"))
        bar_type = _volume_bar_type(row)
        if bar_type == "accumulation":
            _add_level(
                levels, low, current, "大量紅K低點", 3.0, "support", "accumulation_low"
            )
            _add_swing_high(levels, high, current, rows, "大量紅K高點", 2.2)
        elif bar_type == "distribution":
            _add_level(
                levels,
                high,
                current,
                "大量黑K高點",
                3.5,
                "resistance",
                "distribution_high",
            )
            _add_level(
                levels, low, current, "大量黑K低點恐慌區", 1.2, "support", "panic_low"
            )
        else:
            _add_level(
                levels, low, current, "大量K低點", 1.8, "support", "mixed_volume_low"
            )
            _add_level(
                levels,
                high,
                current,
                "大量K高點",
                1.8,
                "resistance",
                "mixed_volume_high",
            )

    # A strong breakout can move above every historical profile, swing high,
    # and previous-session Pivot resistance.  The latest completed OHLCV bar
    # still contains an observed high/low boundary; keep it as a modest level
    # so price discovery is not misclassified as missing market data.
    if rows:
        latest = rows[-1]
        _add_level(
            levels,
            parse_num(latest.get("low")),
            current,
            "最近交易日低點",
            1.8,
            "support",
            "latest_session_low",
        )
        _add_level(
            levels,
            parse_num(latest.get("high")),
            current,
            "最近交易日高點",
            1.8,
            "resistance",
            "latest_session_high",
        )

    pivots = calc_pivot_from_rows(rows)
    for key in ("S1", "S2"):
        _add_level(levels, pivots.get(key), current, f"盤中Pivot {key}", 1.2, "support")
    for key in ("R1", "R2"):
        _add_level(
            levels, pivots.get(key), current, f"盤中Pivot {key}", 1.2, "resistance"
        )

    if highs60 and lows60:
        low60, high60 = min(lows60), max(highs60)
        if high60 > low60:
            if current > (ma20 or current) and ma20 is not None and ma60 is not None and ma20 > ma60:
                for ratio in (0.382, 0.5, 0.618):
                    level = high60 - (high60 - low60) * ratio
                    _add_level(
                        levels,
                        level,
                        current,
                        f"上升回檔Fib {int(ratio * 1000) / 10:g}%",
                        1.0,
                        "support",
                        "fib_up_retracement",
                    )
            elif current < (ma20 or current) and ma20 is not None and ma60 is not None and ma20 < ma60:
                for ratio in (0.382, 0.5, 0.618):
                    level = low60 + (high60 - low60) * ratio
                    _add_level(
                        levels,
                        level,
                        current,
                        f"下降反彈Fib {int(ratio * 1000) / 10:g}%",
                        1.0,
                        "resistance",
                        "fib_down_rebound",
                    )
            else:
                for ratio in (0.382, 0.5, 0.618):
                    level = low60 + (high60 - low60) * ratio
                    _add_level(
                        levels,
                        level,
                        current,
                        f"盤整Fib {int(ratio * 1000) / 10:g}%",
                        0.5,
                    )
    return levels


def period_support_resistance_fields(rows_asc: list[dict[str, Any]]) -> dict[str, str]:
    # Simple homepage levels: latest day and recent 5-day high/low.
    def price_value(row: dict[str, Any], field: str) -> float | None:
        adjusted = row.get(f"technical_{field}")
        return parse_num(adjusted if adjusted is not None else row.get(field))

    usable = [r for r in rows_asc if price_value(r, "high") is not None and price_value(r, "low") is not None]
    if not usable:
        return {
            "today_support": "璩囨枡缂哄彛",
            "today_resistance": "璩囨枡缂哄彛",
            "support_5d": "璩囨枡缂哄彛",
            "resistance_5d": "璩囨枡缂哄彛",
        }
    latest = usable[-1]
    recent5 = usable[-5:] if len(usable) >= 5 else usable
    lows = [price_value(r, "low") for r in recent5]
    highs = [price_value(r, "high") for r in recent5]
    lows = [x for x in lows if x is not None]
    highs = [x for x in highs if x is not None]
    return {
        "today_support": fmt(price_value(latest, "low")),
        "today_resistance": fmt(price_value(latest, "high")),
        "support_5d": fmt(min(lows) if lows else None),
        "resistance_5d": fmt(max(highs) if highs else None),
    }


def zone_position(current: float | None, zone: dict[str, Any] | None, side: str) -> dict[str, Any]:
    # Return current price relation to a support/resistance zone.
    if current is None or not zone:
        return {'state': 'no_data', 'text': 'no data', 'distance_pct': None}
    low = parse_num(zone.get('zone_low') if zone.get('zone_low') is not None else zone.get('price'))
    high = parse_num(zone.get('zone_high') if zone.get('zone_high') is not None else zone.get('price'))
    if low is None or high is None:
        return {'state': 'no_data', 'text': '無資料', 'distance_pct': None}
    if low > high:
        low, high = high, low
    current = float(current)
    if side == 'support':
        if low <= current <= high:
            dist = (high - current) / current * 100
            return {'state': 'inside', 'text': f'已進入支撐區，距上緣 {dist:.1f}%', 'distance_pct': dist}
        if current > high:
            dist = (current - high) / current * 100
            return {'state': 'above', 'text': f'{dist:.1f}%', 'distance_pct': dist}
        return {'state': 'broken', 'text': '已跌破支撐區', 'distance_pct': None}
    if side == 'resistance':
        if low <= current <= high:
            dist = (current - low) / current * 100
            return {'state': 'inside', 'text': f'已進入賣壓區，距下緣 {dist:.1f}%', 'distance_pct': dist}
        if current < low:
            dist = (low - current) / current * 100
            return {'state': 'below', 'text': f'+{dist:.1f}%', 'distance_pct': dist}
        return {'state': 'broken_up', 'text': '已突破賣壓區', 'distance_pct': None}
    return {'state': 'no_data', 'text': '無資料', 'distance_pct': None}


def format_zone_display(label: str, zone: dict[str, Any] | None, pos: dict[str, Any]) -> str:
    if not zone:
        return f'{label} 需先更新日K'
    if zone.get('label_price'):
        price_txt = str(zone.get('label_price'))
    elif zone.get('zone_low') is not None:
        price_txt = f"{fmt(zone.get('zone_low'))}~{fmt(zone.get('zone_high'))}"
    else:
        price_txt = fmt(zone.get('price'))
    strength = zone.get('strength') or ''
    return f"{label} {price_txt} {strength}（{pos.get('text','')}）"


def choose_stop_loss_candidate(close: float | None, support_zone: dict[str, Any] | None, prev_10d_low: float | None, ma20: float | None, atr14: float | None) -> tuple[float | None, str]:
    if close is None:
        return None, '無現價'
    cands: list[tuple[str, float]] = []
    if support_zone:
        low = parse_num(support_zone.get('zone_low') if support_zone.get('zone_low') is not None else support_zone.get('price'))
        if low is not None and low < close:
            cands.append(('支撐區下緣', low))
    if prev_10d_low is not None and prev_10d_low < close:
        cands.append(('前10日低點', float(prev_10d_low)))
    if ma20 is not None and atr14 is not None and ma20 < close:
        cands.append(('MA20-ATR', float(ma20 - atr14)))
    if atr14 is not None:
        cands.append(('2ATR防守', float(close - 2 * atr14)))
    if not cands:
        return None, '無有效停損基準'
    label, level = max(cands, key=lambda x: x[1])
    return level, label


def _finite_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    return out if math.isfinite(out) else None


def _extract_zone_bounds(zone: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not zone:
        return None, None
    low = parse_num(zone.get('zone_low') if zone.get('zone_low') is not None else zone.get('price'))
    high = parse_num(zone.get('zone_high') if zone.get('zone_high') is not None else zone.get('price'))
    if low is None or high is None:
        return None, None
    low_f = _finite_float(low)
    high_f = _finite_float(high)
    if low_f is None or high_f is None:
        return None, None
    if low_f > high_f:
        low_f, high_f = high_f, low_f
    return low_f, high_f


def _zone_bounds_text(zone: dict[str, Any] | None) -> str:
    if not zone:
        return "--"
    low = parse_num(zone.get("zone_low") if zone.get("zone_low") is not None else zone.get("price"))
    high = parse_num(zone.get("zone_high") if zone.get("zone_high") is not None else zone.get("price"))
    if low is None and high is None:
        return str(zone.get("label_price") or "--")
    if low is None:
        low = high
    if high is None:
        high = low
    if low is not None and high is not None and low > high:
        low, high = high, low
    if low == high:
        return fmt(low)
    return f"{fmt(low)}~{fmt(high)}"


def _zone_low_high(zone: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not zone:
        return None, None
    low = parse_num(zone.get("zone_low") if zone.get("zone_low") is not None else zone.get("price"))
    high = parse_num(zone.get("zone_high") if zone.get("zone_high") is not None else zone.get("price"))
    if low is not None and high is not None and low > high:
        low, high = high, low
    return low, high


def _next_lower_support_text(sr_detail: dict[str, Any] | None, current_support: dict[str, Any] | None, price: float | None) -> str:
    if not sr_detail:
        return "下一層支撐尚未形成可靠共振"
    cur_low, cur_high = _zone_low_high(current_support)
    supports = sr_detail.get("supports") or []
    candidates: list[dict[str, Any]] = []
    for z in supports:
        low, high = _zone_low_high(z)
        if low is None and high is None:
            continue
        ref = high if high is not None else low
        if cur_low is not None and ref is not None and ref >= cur_low * 0.995:
            continue
        if price is not None and ref is not None and ref >= float(price) * 0.995:
            continue
        candidates.append(z)
    if not candidates:
        return "下一層支撐尚未形成可靠共振"
    z = candidates[0]
    strength = z.get("strength") or ""
    return f"{_zone_bounds_text(z)}{(' ' + str(strength)) if strength else ''}"

