from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

SYSTEM_VERSION = "v1.0.4"


def get_indicator_value_for_scoring(
    chip_result: dict,
    key: str,
    allow_low_confidence: bool = True,
) -> Optional[float]:
    item = chip_result.get(key, {}) if isinstance(chip_result, dict) else {}
    value = item.get("value")
    confidence = item.get("confidence", "invalid")
    if value is None:
        return None
    if confidence in {"invalid", "none"}:
        return None
    if confidence == "low" and not allow_low_confidence:
        return None
    try:
        return float(value)
    except Exception:
        return None


def confidence_weight(chip_result: dict, key: str) -> float:
    item = chip_result.get(key, {}) if isinstance(chip_result, dict) else {}
    confidence = item.get("confidence", "invalid")
    if confidence in {"medium", "high"}:
        return 1.0
    if confidence == "low":
        return 0.5
    return 0.0


def apply_chip_cost_score(score: float, current_price: float, chip_result: dict) -> float:
    """Canonical costs are background-only and have a fixed score weight of zero."""

    _ = current_price, chip_result
    return score


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").astype(float)


def _safe_div(a: float, b: float) -> float:
    if not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return float("nan")
    return float(a / b)


def _last(s: pd.Series, offset: int = -1) -> float:
    try:
        v = s.iloc[offset]
    except Exception:
        return float("nan")
    return float(v) if pd.notna(v) else float("nan")


def _finite(*values: float) -> bool:
    return all(np.isfinite(v) for v in values)


def _slope(values: pd.Series | np.ndarray | list[float]) -> float:
    arr = np.asarray(values, dtype="float64")
    arr = arr[np.isfinite(arr)]
    if len(arr) < 2:
        return float("nan")
    return float(np.polyfit(np.arange(len(arr)), arr, 1)[0])


def _ema_with_sma_seed(series: pd.Series, period: int) -> pd.Series:
    """EMA seeded by the first N valid values' SMA, then recursively smoothed.

    This avoids pandas.ewm()'s default first-observation seed and matches the
    requested hand-rolled EMA definition: k = 2 / (N + 1).
    """
    s = _to_num(series)
    out = pd.Series(np.nan, index=s.index, dtype="float64")
    valid_positions = np.flatnonzero(s.notna().to_numpy())
    if len(valid_positions) < period:
        return out
    seed_pos = int(valid_positions[period - 1])
    seed = s.iloc[valid_positions[:period]].mean()
    seeded = pd.Series(np.nan, index=s.index, dtype="float64")
    seeded.iloc[seed_pos] = seed
    seeded.iloc[seed_pos + 1 :] = s.iloc[seed_pos + 1 :]
    return seeded.ewm(alpha=2 / (period + 1), adjust=False, min_periods=1).mean()


def _wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    # TODO(accurate-data-source, 2026-06-19):
    # Ensure displayed price/technical values come from a clear authoritative source or documented local formula.
    # Goodinfo/Yahoo/WantGoo are manual verification references only, not scraping targets or frontend comparison fields.
    """Wilder RSI with initial avg gain/loss seeded by SMA of first N diffs."""
    c = _to_num(close)
    delta = c.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    rsi = pd.Series(np.nan, index=c.index, dtype="float64")
    if len(c) < period + 1:
        return rsi

    gain_seed = gain.iloc[1 : period + 1].mean()
    loss_seed = loss.iloc[1 : period + 1].mean()
    gain_input = pd.Series(np.nan, index=c.index, dtype="float64")
    loss_input = pd.Series(np.nan, index=c.index, dtype="float64")
    gain_input.iloc[period] = gain_seed
    loss_input.iloc[period] = loss_seed
    gain_input.iloc[period + 1 :] = gain.iloc[period + 1 :]
    loss_input.iloc[period + 1 :] = loss.iloc[period + 1 :]

    avg_gain = gain_input.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()
    avg_loss = loss_input.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return rsi


def wilder_rsi_value(
    close_values: list[float | int | None],
    period: int,
    *,
    max_input_rows: int = 120,
) -> float | None:
    """Return the latest Wilder RSI using the Dashboard's exact seed rules.

    This scalar form is used by historical snapshot materialization.  Keeping
    it in the canonical scoring module prevents the database worker from
    introducing a second RSI definition while avoiding a Pandas allocation
    for every stock/date pair.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    values = close_values[-max_input_rows:] if max_input_rows > 0 else close_values
    parsed: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(number):
            return None
        parsed.append(number)
    if len(parsed) < period + 1:
        return None

    changes = [parsed[index] - parsed[index - 1] for index in range(1, len(parsed))]
    avg_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    avg_loss = sum(max(-change, 0.0) for change in changes[:period]) / period
    for change in changes[period:]:
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = ((period - 1) * avg_gain + gain) / period
        avg_loss = ((period - 1) * avg_loss + loss) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def _kd_from_rsv(rsv: pd.Series) -> tuple[pd.Series, pd.Series]:
    # TODO(accurate-data-source, 2026-06-19):
    # Ensure displayed price/technical values come from a clear authoritative source or documented local formula.
    # Goodinfo/Yahoo/WantGoo are manual verification references only, not scraping targets or frontend comparison fields.
    """KD with K=D=50 seed using recursive 2/3 previous + 1/3 current."""
    k = pd.Series(np.nan, index=rsv.index, dtype="float64")
    d = pd.Series(np.nan, index=rsv.index, dtype="float64")
    valid = rsv.dropna().astype(float)
    if valid.empty:
        return k, d
    seed = pd.Series([50.0], index=["__seed__"])
    k_seq = pd.concat([seed, valid]).ewm(alpha=1 / 3, adjust=False, min_periods=1).mean().iloc[1:]
    k.loc[valid.index] = k_seq.to_numpy()
    d_seq = pd.concat([seed, k.loc[valid.index]]).ewm(alpha=1 / 3, adjust=False, min_periods=1).mean().iloc[1:]
    d.loc[valid.index] = d_seq.to_numpy()
    return k, d


def _cross_count(a: pd.Series, b: pd.Series, days: int) -> int:
    rel = (a.tail(days) > b.tail(days)).astype(int)
    return int(rel.diff().abs().fillna(0).sum())


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    # TODO(accurate-data-source, 2026-06-19):
    # Ensure displayed price/technical values come from a clear authoritative source or documented local formula.
    # Goodinfo/Yahoo/WantGoo are manual verification references only, not scraping targets or frontend comparison fields.
    """Return df with all technical indicators required by scoring v1.0.

    Required columns are ``date, open, high, low, close, volume`` sorted from old
    to new. Raw OHLC columns remain unchanged for display.  When corresponding
    ``technical_*`` columns are present, every price-based indicator uses those
    split-adjusted values. EMA is seeded with first N-day SMA; RSI uses Wilder
    smoothing; all rolling windows use completed row values. ``previous_*``
    levels deliberately exclude the current day by shifting one row.
    """
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"df missing required columns: {sorted(missing)}")

    out = df.copy().reset_index(drop=True)
    out["date"] = out["date"].astype(str)
    for col in ["open", "high", "low", "close", "volume"]:
        out[col] = _to_num(out[col])

    technical_price: dict[str, pd.Series] = {}
    for field in ("open", "high", "low", "close"):
        technical_col = f"technical_{field}"
        if technical_col in out.columns:
            out[technical_col] = _to_num(out[technical_col])
            technical_price[field] = out[technical_col].where(
                out[technical_col].notna(), out[field]
            )
        else:
            technical_price[field] = out[field]

    technical_high = technical_price["high"]
    technical_low = technical_price["low"]
    technical_close = technical_price["close"]

    out["ema12"] = _ema_with_sma_seed(technical_close, 12)
    out["ema26"] = _ema_with_sma_seed(technical_close, 26)
    out["dif"] = out["ema12"] - out["ema26"]
    out["macd_signal"] = _ema_with_sma_seed(out["dif"], 9)
    out["osc"] = out["dif"] - out["macd_signal"]

    if "technical_close" in out.columns:
        rsi_source = technical_close
    elif "rsi_close" in out.columns:
        rsi_source = _to_num(out["rsi_close"])
    else:
        rsi_source = out["close"]
    rsi_source = rsi_source.tail(120)
    out["rsi5"] = _wilder_rsi(rsi_source, 5)
    out["rsi10"] = _wilder_rsi(rsi_source, 10)
    out["rsi14"] = _wilder_rsi(rsi_source, 14)

    low9 = technical_low.rolling(9, min_periods=9).min()
    high9 = technical_high.rolling(9, min_periods=9).max()
    out["rsv9"] = (technical_close - low9) / (high9 - low9).replace(0, np.nan) * 100
    out["k"], out["d"] = _kd_from_rsv(out["rsv9"])

    out["ma5"] = technical_close.rolling(5, min_periods=5).mean()
    out["ma10"] = technical_close.rolling(10, min_periods=10).mean()
    out["ma20"] = technical_close.rolling(20, min_periods=20).mean()
    out["ma60"] = technical_close.rolling(60, min_periods=60).mean()
    out["vol_ma5"] = out["volume"].rolling(5, min_periods=5).mean()
    out["vol_ma20"] = out["volume"].rolling(20, min_periods=20).mean()

    direction = np.sign(technical_close.diff()).fillna(0)
    out["obv"] = (direction * out["volume"]).cumsum()

    prev_close = technical_close.shift(1)
    tr = pd.concat([
        technical_high - technical_low,
        (technical_high - prev_close).abs(),
        (technical_low - prev_close).abs(),
    ], axis=1).max(axis=1)
    # Wilder ATR seeded by first 14 TR values' SMA.
    atr_input = pd.Series(np.nan, index=out.index, dtype="float64")
    if len(tr) >= 14:
        atr_input.iloc[13] = tr.iloc[:14].mean()
        atr_input.iloc[14:] = tr.iloc[14:]
    out["atr14"] = atr_input.ewm(alpha=1 / 14, adjust=False, min_periods=1).mean()

    out["boll_mid"] = out["ma20"]
    sigma = technical_close.rolling(20, min_periods=20).std(ddof=0)
    out["boll_upper"] = out["boll_mid"] + 2 * sigma
    out["boll_lower"] = out["boll_mid"] - 2 * sigma
    out["boll_width"] = (out["boll_upper"] - out["boll_lower"]) / out["boll_mid"].replace(0, np.nan)

    out["previous_3d_low"] = technical_low.rolling(3, min_periods=3).min().shift(1)
    out["previous_10d_low"] = technical_low.rolling(10, min_periods=10).min().shift(1)
    out["previous_20d_low"] = technical_low.rolling(20, min_periods=20).min().shift(1)
    out["previous_20d_high"] = technical_high.rolling(20, min_periods=20).max().shift(1)
    out["previous_60d_high"] = technical_high.rolling(60, min_periods=60).max().shift(1)
    return out


def _validate_data(df: pd.DataFrame) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required.difference(df.columns)
    if missing:
        reasons.append(f"缺少必要欄位: {sorted(missing)}")
        return False, reasons
    if len(df) < 120:
        reasons.append("資料筆數少於120")
    dates = pd.to_datetime(df["date"], errors="coerce")
    if dates.isna().any():
        reasons.append("日期格式異常")
    if not dates.is_monotonic_increasing:
        reasons.append("日期未由舊到新排序")
    if dates.duplicated().any():
        reasons.append("日期重複")
    tmp = df.copy()
    for col in ["open", "high", "low", "close", "volume"]:
        tmp[col] = _to_num(tmp[col])
    # volume 單筆缺值用中位數填補，不因單筆 NULL 拒絕整支股票
    if tmp["volume"].isna().any():
        med = tmp["volume"].median()
        tmp["volume"] = tmp["volume"].fillna(med if pd.notna(med) else 0)
    if tmp[["open", "high", "low", "close"]].isna().any().any():
        reasons.append("價格有缺值")
    if tmp["volume"].isna().any():
        reasons.append("成交量有缺值")
    if (tmp["high"] < tmp["low"]).any():
        reasons.append("high < low")
    if ((tmp["close"] > tmp["high"]) | (tmp["close"] < tmp["low"])).any():
        reasons.append("close 不在 high/low 範圍內")
    if ((tmp["open"] > tmp["high"]) | (tmp["open"] < tmp["low"])).any():
        reasons.append("open 不在 high/low 範圍內")
    if (tmp["volume"] < 0).any():
        reasons.append("volume < 0")
    return len(reasons) == 0, reasons


def _score_first(rules: list[tuple[bool, float, str]], default: tuple[float, str]) -> tuple[float, str]:
    for cond, score, reason in rules:
        if bool(cond):
            return float(score), reason
    return float(default[0]), default[1]


def _general_signal(total: float) -> str:
    if total >= 85:
        return "強勢多頭，進場位置佳"
    if total >= 70:
        return "偏多，可列入試單觀察"
    if total >= 55:
        return "趨勢尚可，等待更好位置"
    if total >= 40:
        return "訊號混沌，觀望"
    return "偏弱，避免做多"


def _trend_state_from_df(df: pd.DataFrame | None) -> str:
    """Return bullish/neutral/bearish/no_data for market or sector context.

    Missing context is intentionally no_data, not neutral. This prevents
    eligible_for_watchlist from silently treating unavailable market/sector
    confirmation as a pass.
    """
    if df is None or len(df) < 60:
        return "no_data"
    try:
        ind = calculate_indicators(df.copy())
        close = _last(ind["close"])
        ma20 = _last(ind["ma20"])
        ma60 = _last(ind["ma60"])
        ma20_5 = _last(ind["ma20"], -5)
        ma60_10 = _last(ind["ma60"], -10)
        if _finite(close, ma20, ma60, ma20_5, ma60_10):
            if close > ma20 > ma60 and ma20 > ma20_5:
                return "bullish"
            if close < ma20 < ma60 and ma20 < ma20_5 and ma60 < ma60_10:
                return "bearish"
            return "neutral"
    except Exception:
        return "no_data"
    return "no_data"


def score_stock(
    df: pd.DataFrame,
    market_df: pd.DataFrame | None = None,
    sector_df: pd.DataFrame | None = None,
    event_data: dict[str, Any] | None = None,
    position_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a stock with the technical scoring system v1.0.

    Parameters
    ----------
    df:
        Daily OHLCV DataFrame sorted by date old-to-new. Required columns:
        ``date, open, high, low, close, volume``. At least 120 rows are required;
        250+ rows gives ``data_level='full'``.
    market_df, sector_df:
        Optional daily OHLCV data for market/sector confirmation. Missing data is
        reported as ``no_data`` and is never assumed normal.
    event_data:
        Optional event-state dictionary. Supported key: ``event_risk_state``.
        Missing event data returns ``unknown``.
    position_data:
        Optional holding information for the separated exit module. If omitted,
        exit fields stay inactive.

    Returns
    -------
    dict[str, Any]
        The v1.0 output schema with trend/entry/volume/risk scores, states,
        stop-loss candidate, target candidate, risk-reward state, veto reasons,
        watchlist eligibility and optional exit flags.
    """
    data_valid, data_reasons = _validate_data(df)
    if not data_valid:
        last_close = float(_to_num(df["close"]).iloc[-1]) if "close" in df.columns and len(df) else float("nan")
        return {
            "system_version": SYSTEM_VERSION,
            "date": str(df["date"].iloc[-1]) if "date" in df.columns and len(df) else "",
            "close": last_close,
            "data_valid": False,
            "data_level": "invalid",
            "liquidity_state": "unknown",
            "scores": {"trend_score": 0.0, "entry_score": 0.0, "volume_score": 0.0, "risk_score": 0.0, "technical_total": 0.0},
            "states": {"macd_state": "unknown", "rsi_state": "unknown", "kd_state": "unknown", "setup_type": "unknown", "stop_risk_state": "unknown", "market_state": "no_data", "weekly_state": "insufficient_data", "sector_state": "no_data", "event_risk_state": "unknown", "rr_state": "unknown"},
            "stop_loss_candidate": None,
            "target_price": None,
            "risk_reward_ratio": None,
            "veto": False,
            "veto_reasons": [],
            "eligible_for_watchlist": False,
            "signal": "資料異常｜不評分",
            "reasons": data_reasons,
            "exit_required": False,
            "exit_level": None,
            "exit_reason": None,
            "reduce_position": False,
            "reason": "；".join(data_reasons),
            "total": 0.0,
        }

    ind = calculate_indicators(df)
    n = len(ind)
    data_level = "full" if n >= 250 else "partial"

    close = _last(ind["close"]); close2 = _last(ind["close"], -2)
    open_ = _last(ind["open"]); high = _last(ind["high"]); low = _last(ind["low"])
    volume = _last(ind["volume"])
    ma20 = _last(ind["ma20"]); ma60 = _last(ind["ma60"]); atr14 = _last(ind["atr14"])
    dif = _last(ind["dif"]); dif2 = _last(ind["dif"], -2)
    macd = _last(ind["macd_signal"]); macd2 = _last(ind["macd_signal"], -2)
    osc = ind["osc"]
    rsi10 = _last(ind["rsi10"]); rsi10_2 = _last(ind["rsi10"], -2); rsi10_3 = _last(ind["rsi10"], -3)
    k = _last(ind["k"]); k2 = _last(ind["k"], -2); d = _last(ind["d"]); d2 = _last(ind["d"], -2)
    vol_ma5 = _last(ind["vol_ma5"]); vol_ma20 = _last(ind["vol_ma20"])
    prev_3d_low = _last(ind["previous_3d_low"]); prev_10d_low = _last(ind["previous_10d_low"])
    prev_20d_high = _last(ind["previous_20d_high"]); prev_60d_high = _last(ind["previous_60d_high"])
    boll_upper = _last(ind["boll_upper"])
    recent_vol = _to_num(ind["volume"]).tail(3)
    last2_zero_volume = len(recent_vol) >= 2 and bool((recent_vol.tail(2) == 0).all())
    last3_zero_volume = len(recent_vol) >= 3 and bool((recent_vol.tail(3) == 0).all())
    volume_quality_warnings: list[str] = []

    turnover = ind["close"] * ind["volume"]
    avg_turnover_20 = float(turnover.tail(20).mean())
    if avg_turnover_20 < 30_000_000:
        liquidity_state = "insufficient"
    elif avg_turnover_20 < 100_000_000:
        liquidity_state = "low"
    else:
        liquidity_state = "normal"
    if last3_zero_volume:
        data_level = "volume_suspended"
        liquidity_state = "volume_suspended"
        volume_quality_warnings.append("近3日成交量為0，量能/OBV指標降級")
    elif last2_zero_volume:
        if data_level == "full":
            data_level = "partial"
        volume_quality_warnings.append("近2日成交量為0，量能/OBV指標需保守解讀")

    ma20_up_5d = _finite(ma20, _last(ind["ma20"], -5)) and ma20 > _last(ind["ma20"], -5)
    ma60_up_10d = _finite(ma60, _last(ind["ma60"], -10)) and ma60 > _last(ind["ma60"], -10)
    ma20_change_5d = _safe_div(ma20 - _last(ind["ma20"], -5), _last(ind["ma20"], -5))
    ma20_change_20d = _safe_div(ma20 - _last(ind["ma20"], -20), _last(ind["ma20"], -20))
    ma60_change_10d = _safe_div(ma60 - _last(ind["ma60"], -10), _last(ind["ma60"], -10))
    ma60_change_20d = _safe_div(ma60 - _last(ind["ma60"], -20), _last(ind["ma60"], -20))
    above_ma20_count_5d = int((ind["close"].tail(5) > ind["ma20"].tail(5)).sum())

    macd_golden_cross = _finite(dif2, macd2, dif, macd) and dif2 <= macd2 and dif > macd
    macd_death_cross = _finite(dif2, macd2, dif, macd) and dif2 >= macd2 and dif < macd
    macd_bullish_hold = _finite(dif2, macd2, dif, macd) and dif2 > macd2 and dif > macd
    macd_bearish_hold = _finite(dif2, macd2, dif, macd) and dif2 < macd2 and dif < macd
    osc_improving_5d = len(osc) >= 5 and _finite(_last(osc), _last(osc, -3), _last(osc, -5)) and _last(osc) > _last(osc, -3) > _last(osc, -5)
    osc_worsening_5d = len(osc) >= 5 and _finite(_last(osc), _last(osc, -3), _last(osc, -5)) and _last(osc) < _last(osc, -3) < _last(osc, -5)
    dif_slope_10d = _slope(ind["dif"].tail(10))
    dif_slope_20d = _slope(ind["dif"].tail(20))
    macd_cross_count_20d = _cross_count(ind["dif"], ind["macd_signal"], 20)

    rsi_cross_up_50 = _finite(rsi10_2, rsi10) and rsi10_2 <= 50 < rsi10
    rsi_cross_down_50 = _finite(rsi10_2, rsi10) and rsi10_2 >= 50 > rsi10
    rsi_rising_3d = _finite(rsi10, rsi10_2, rsi10_3) and rsi10 > rsi10_2 > rsi10_3
    rsi_hold_above_50_5d = int((ind["rsi10"].tail(5) > 50).sum()) >= 4
    rsi_high_count_10d = int((ind["rsi10"].tail(10) > 70).sum())

    kd_golden_cross = _finite(k2, d2, k, d) and k2 <= d2 and k > d
    kd_death_cross = _finite(k2, d2, k, d) and k2 >= d2 and k < d
    k_rising_3d = _finite(k, k2, _last(ind["k"], -3)) and k > k2 > _last(ind["k"], -3)
    d_rising_3d = _finite(d, d2, _last(ind["d"], -3)) and d > d2 > _last(ind["d"], -3)
    kd_cross_count_14d = _cross_count(ind["k"], ind["d"], 14)

    distance_atr = _safe_div(close - ma20, atr14)
    daily_range = high - low if _finite(high, low) else float("nan")
    close_near_high = _finite(daily_range) and daily_range > 0 and _safe_div(close - low, daily_range) >= 0.7
    breakout_20d = _finite(close, prev_20d_high) and close > prev_20d_high
    false_breakout_20d = _finite(high, close, prev_20d_high) and high > prev_20d_high and close <= prev_20d_high
    pullback_reclaim_ma20 = bool(ma20_up_5d and _finite(low, ma20, close, close2) and low <= ma20 and close > ma20 and close > close2)
    near_ma20 = _finite(distance_atr) and -1.0 <= distance_atr <= 1.0
    break_ma20_without_recovery = _finite(close, close2, ma20, _last(ind["ma20"], -2)) and close < ma20 and close2 < _last(ind["ma20"], -2) and close <= close2
    volume_ratio_5 = _safe_div(volume, vol_ma5)
    volume_ratio_20 = _safe_div(volume, vol_ma20)
    volume_trend_up = _finite(vol_ma5, vol_ma20, _last(ind["vol_ma5"], -5)) and vol_ma5 > vol_ma20 and vol_ma5 > _last(ind["vol_ma5"], -5)

    obv_slope_10d = _slope(ind["obv"].tail(10)); obv_slope_20d = _slope(ind["obv"].tail(20))
    close_slope_10d = _slope(ind["close"].tail(10)); close_slope_20d = _slope(ind["close"].tail(20))
    obv_slope_10d_norm = _safe_div(obv_slope_10d, vol_ma20)
    obv_slope_20d_norm = _safe_div(obv_slope_20d, vol_ma20)
    obv_flat = _finite(obv_slope_10d_norm, obv_slope_20d_norm) and abs(obv_slope_10d_norm) < 0.10 and abs(obv_slope_20d_norm) < 0.10
    close_return_10d = _safe_div(close, _last(ind["close"], -10)) - 1 if _finite(_last(ind["close"], -10)) else float("nan")
    price_flat_or_slight_decline = _finite(close_return_10d) and -0.02 <= close_return_10d <= 0.005

    # Trend score 0-40
    ma_struct_score, ma_struct_reason = _score_first([
        (_finite(close, ma20, ma60) and close > ma20 > ma60 and ma20_up_5d and ma60_up_10d and above_ma20_count_5d >= 4, 14, "均線多頭排列且MA20/MA60同步上彎"),
        (_finite(close, ma20, ma60) and close > ma20 > ma60, 11, "價格站上MA20且MA20高於MA60"),
        (_finite(close, ma20, ma60) and close > ma20 and ma20 <= ma60 and ma20_up_5d, 8, "價格站上上彎MA20但中期均線尚未翻多"),
        (_finite(close, ma20, ma60) and close < ma20 and ma20 > ma60 and ma60_up_10d, 5, "跌破MA20但中期結構仍在"),
        (_finite(close, ma20, ma60) and close < ma20 < ma60, 0, "均線空頭排列"),
    ], (3, "均線結構普通"))
    ma20_dir_score, ma20_dir_reason = _score_first([
        (_finite(ma20_change_5d, ma20_change_20d) and ma20_change_5d > 0.005 and ma20_change_20d > 0.005, 8, "MA20中短期明確上升"),
        (_finite(ma20_change_5d, ma20_change_20d) and ma20_change_5d > 0 and ma20_change_20d > 0, 6, "MA20維持上升"),
        (_finite(ma20_change_5d, ma20_change_20d) and ma20_change_5d > 0 and ma20_change_20d <= 0, 4, "MA20短線轉強"),
        (_finite(ma20_change_5d, ma20_change_20d) and ma20_change_5d <= 0 and ma20_change_20d > 0, 2, "MA20中期仍偏多但短線轉弱"),
    ], (0, "MA20方向偏弱"))
    ma60_dir_score, ma60_dir_reason = _score_first([
        (_finite(ma60_change_10d, ma60_change_20d) and ma60_change_10d > 0 and ma60_change_20d > 0, 6, "MA60中期上升"),
        (_finite(ma60_change_10d, ma60_change_20d) and ma60_change_10d > 0 and ma60_change_20d <= 0, 3, "MA60短期轉強"),
        (_finite(ma60_change_10d, ma60_change_20d) and ma60_change_10d <= 0 and ma60_change_20d > 0, 2, "MA60長一點仍偏多但短期轉弱"),
    ], (0, "MA60方向偏弱"))
    macd_score, macd_reason = _score_first([
        (macd_golden_cross and dif > 0 and osc_improving_5d and dif_slope_10d > 0 and macd_cross_count_20d <= 2, 12, "MACD零軸上黃金交叉且動能改善"),
        (macd_bullish_hold and dif > 0 and osc_improving_5d and dif_slope_10d > 0, 11, "MACD多方延續且DIF斜率向上"),
        (macd_bullish_hold and dif > 0, 8, "MACD維持零軸上多方"),
        (macd_golden_cross and dif <= 0 and osc_improving_5d, 6, "MACD零軸下黃金交叉且動能改善"),
        (macd_bullish_hold and dif <= 0, 4, "MACD仍在零軸下但多方維持"),
        (macd_death_cross and dif > 0, 3, "MACD零軸上死亡交叉"),
        (macd_bearish_hold and osc_improving_5d, 2, "MACD偏空但OSC改善"),
        (macd_death_cross and dif <= 0, 0, "MACD零軸下死亡交叉"),
        (macd_bearish_hold and osc_worsening_5d, 0, "MACD偏空且動能惡化"),
    ], (1, "MACD狀態不明確"))
    if macd_cross_count_20d >= 4 and macd_score > 6:
        macd_score = 6.0
        macd_reason += "；20日交叉過多，分數上限6"
    trend_score = ma_struct_score + ma20_dir_score + ma60_dir_score + macd_score

    # Entry score 0-35
    rsi_score, rsi_reason = _score_first([
        (rsi_cross_up_50 and rsi_rising_3d and rsi10 <= 65, 10, "RSI10上穿50且三日走升"),
        (_finite(rsi10) and 50 < rsi10 <= 65 and rsi_hold_above_50_5d and rsi_rising_3d, 9, "RSI10站穩50上且三日走升"),
        (_finite(rsi10, rsi10_2) and 50 < rsi10 <= 65 and rsi10 > rsi10_2, 8, "RSI10在多方區且上升"),
        (_finite(rsi10) and 45 <= rsi10 <= 50 and rsi_rising_3d, 6, "RSI10接近50且轉強"),
        (_finite(rsi10) and 65 < rsi10 <= 70, 5, "RSI10偏強但稍高"),
        (_finite(rsi10) and 70 < rsi10 <= 75, 3, "RSI10過熱風險升高"),
        (_finite(rsi10) and rsi10 > 75, 0, "RSI10過熱"),
        (rsi_cross_down_50, 0, "RSI10跌破50"),
        (_finite(rsi10) and rsi10 < 45, 0, "RSI10弱勢"),
    ], (2, "RSI10狀態普通"))
    if rsi_high_count_10d >= 5 and rsi_score > 3:
        rsi_score = 3.0
        rsi_reason += "；近10日高檔天數過多，上限3"
    distance_score, distance_reason = _score_first([
        (_finite(distance_atr) and 0 <= distance_atr <= 1.0 and ma20_up_5d, 10, "價格在上彎MA20附近上方"),
        (_finite(distance_atr, close, close2) and -1.0 <= distance_atr < 0 and ma20_up_5d and close > close2, 8, "回測MA20附近後反彈"),
        (_finite(distance_atr) and 1.0 < distance_atr <= 2.0, 7, "距離MA20尚可"),
        (_finite(distance_atr) and 2.0 < distance_atr <= 3.0, 3, "距離MA20偏遠"),
        (_finite(distance_atr) and distance_atr > 3.0, 0, "距離MA20過遠"),
        (_finite(distance_atr) and distance_atr < -1.0, 0, "跌離MA20下方"),
    ], (2, "價格距離MA20普通"))
    pattern_score, pattern_reason = _score_first([
        (breakout_20d and _finite(volume_ratio_20) and 1.2 <= volume_ratio_20 <= 2.5 and close_near_high, 10, "20日突破且量能合理收近高"),
        (pullback_reclaim_ma20, 10, "拉回收復MA20"),
        (near_ma20 and ma20_up_5d, 7, "接近上彎MA20"),
        (breakout_20d, 6, "20日突破"),
        (false_breakout_20d, 0, "假突破"),
        (break_ma20_without_recovery, 0, "跌破MA20未收復"),
    ], (2, "無明確突破或拉回型態"))
    kd_score, kd_reason = _score_first([
        (kd_golden_cross and k < 50 and k_rising_3d and d_rising_3d, 5, "KD低中位黃金交叉且K/D同步上升"),
        (kd_golden_cross and 50 <= k < 80, 3, "KD黃金交叉"),
        (kd_golden_cross and k >= 80, 1, "KD高檔黃金交叉"),
        (_finite(k, d) and k > d and k_rising_3d and d_rising_3d, 1, "KD多方延續"),
        (kd_death_cross, 0, "KD死亡交叉"),
    ], (0, "KD無觸發"))
    if kd_cross_count_14d >= 4 and kd_score > 2:
        kd_score = 2.0
        kd_reason += "；14日交叉過多，上限2"
    entry_score = rsi_score + distance_score + pattern_score + kd_score

    # Volume score 0-15
    vol_score, vol_reason = _score_first([
        (_finite(close, close2, volume_ratio_20) and close > close2 and 1.5 <= volume_ratio_20 <= 2.5 and volume_trend_up, 8, "上漲放量且量趨勢向上"),
        (_finite(close, close2, volume_ratio_20) and close > close2 and 1.2 <= volume_ratio_20 < 1.5, 6, "上漲溫和放量"),
        (_finite(close, close2, volume_ratio_20) and close > close2 and 0.8 <= volume_ratio_20 < 1.2, 3, "上漲量能正常"),
        (_finite(close, close2, volume_ratio_20) and close > close2 and volume_ratio_20 < 0.8, 1, "上漲但量能不足"),
        (_finite(close, close2, volume_ratio_20) and close < close2 and volume_ratio_20 > 1.5, 0, "下跌放量"),
    ], (1, "成交量普通"))
    if _finite(volume_ratio_20) and volume_ratio_20 > 3.0 and vol_score > 2:
        vol_score = 2.0
        vol_reason += "；爆量過大，上限2"
    obv_score, obv_reason = _score_first([
        (_finite(obv_slope_10d, obv_slope_20d, close_slope_20d) and obv_slope_10d > 0 and obv_slope_20d > 0 and close_slope_20d > 0, 7, "OBV與價格趨勢同步向上"),
        (_finite(obv_slope_10d, obv_slope_20d) and obv_slope_10d > 0 and obv_slope_20d > 0 and price_flat_or_slight_decline, 6, "價格整理但OBV先行"),
        (_finite(obv_slope_10d, obv_slope_20d) and obv_slope_10d > 0 and obv_slope_20d <= 0, 4, "OBV短線轉強"),
        (obv_flat, 3, "OBV持平"),
        (_finite(obv_slope_10d, close_slope_10d) and obv_slope_10d < 0 and close_slope_10d > 0, 0, "OBV短線背離"),
        (_finite(obv_slope_20d, close_slope_20d) and obv_slope_20d < 0 and close_slope_20d > 0, 0, "OBV中期背離"),
        (_finite(obv_slope_20d, close_slope_20d) and obv_slope_20d < 0 and close_slope_20d < 0, 1, "OBV與價格同步轉弱"),
    ], (2, "OBV狀態普通"))
    volume_score = vol_score + obv_score
    if last3_zero_volume:
        vol_score = 0.0
        obv_score = 0.0
        volume_score = 0.0
        vol_reason = "近3日成交量為0，量能分數停用"
        obv_reason = "近3日成交量為0，OBV分數停用"
    elif last2_zero_volume:
        volume_score = min(float(volume_score), 2.0)
        vol_reason += "；近2日零量，量能分數上限2"

    # Risk score 0-10
    overheat_rsi = _finite(rsi10) and rsi10 >= 70
    overheat_distance = _finite(distance_atr) and distance_atr > 2
    overheat_boll = _finite(close, boll_upper) and close > boll_upper
    overheat_count = int(overheat_rsi) + int(overheat_distance) + int(overheat_boll)
    overheat_score, overheat_reason = _score_first([
        (overheat_count == 0, 4, "無明顯過熱"),
        (overheat_count == 1, 2, "單一過熱條件"),
        (overheat_count >= 2, 0, "多項過熱"),
    ], (0, "過熱狀態不明"))
    macd_bearish_divergence = _finite(close_slope_20d, dif_slope_20d) and close_slope_20d > 0 and dif_slope_20d < 0
    obv_bearish_divergence = _finite(close_slope_20d, obv_slope_20d) and close_slope_20d > 0 and obv_slope_20d < 0
    soft_support_break = _finite(close, ma20) and close < ma20
    hard_support_break = soft_support_break and _finite(close, prev_10d_low) and close < prev_10d_low
    confirmed_support_break = bool(hard_support_break and _finite(volume_ratio_20) and volume_ratio_20 >= 1.5)
    support_break = bool(soft_support_break or hard_support_break)
    divergence_score, divergence_reason = _score_first([
        (not macd_bearish_divergence and not obv_bearish_divergence and not hard_support_break, 3, "無MACD/OBV背離且未破位"),
        (hard_support_break and not (macd_bearish_divergence or obv_bearish_divergence), 1, "跌破MA20與10日低點"),
        ((macd_bearish_divergence or obv_bearish_divergence) and not support_break, 1, "有背離但未跌破支撐"),
        ((macd_bearish_divergence or obv_bearish_divergence) and support_break, 0, "背離且跌破支撐"),
    ], (1, "背離/破位風險不明"))
    atr_pct = _safe_div(atr14, close) * 100 if _finite(atr14, close) else float("nan")
    atr_score, atr_reason = _score_first([
        (_finite(atr_pct) and atr_pct <= 3, 3, "波動風險低"),
        (_finite(atr_pct) and 3 < atr_pct <= 5, 2, "波動風險中等"),
        (_finite(atr_pct) and 5 < atr_pct <= 8, 1, "波動風險偏高"),
        (_finite(atr_pct) and atr_pct > 8, 0, "波動風險高"),
    ], (0, "波動風險不明"))
    risk_score = overheat_score + divergence_score + atr_score

    technical_total = round(float(trend_score + entry_score + volume_score + risk_score), 2)

    setup_type = "breakout" if breakout_20d else ("pullback" if (pullback_reclaim_ma20 or near_ma20) else "other")
    if setup_type == "breakout":
        # v1.0.1: use the previous completed day's low instead of today's
        # still-changing intraday low. This keeps the breakout risk estimate
        # stable during the trading session.
        stop_loss_candidate = _last(ind["low"], -2)
    elif setup_type == "pullback":
        stop_loss_candidate = prev_10d_low
    else:
        stop_loss_candidate = ma20 - atr14 if _finite(ma20, atr14) else float("nan")
    potential_risk = close - stop_loss_candidate if _finite(close, stop_loss_candidate) else float("nan")
    potential_risk_atr = _safe_div(potential_risk, atr14)
    if _finite(potential_risk_atr) and potential_risk_atr <= 1.5:
        stop_risk_state = "good"
    elif _finite(potential_risk_atr) and potential_risk_atr <= 2.5:
        stop_risk_state = "acceptable"
    elif _finite(potential_risk_atr):
        stop_risk_state = "poor"
    else:
        stop_risk_state = "unknown"

    daily_return = _safe_div(close, close2) - 1 if _finite(close, close2) else float("nan")
    volume_breakdown = _finite(daily_return, volume_ratio_20, close, ma20, prev_10d_low) and daily_return <= -0.05 and volume_ratio_20 >= 1.8 and close < ma20 and close < prev_10d_low
    gap_down_breakdown = _finite(high, _last(ind["low"], -2), close, ma20, volume_ratio_20) and high < _last(ind["low"], -2) and close < ma20 and volume_ratio_20 >= 1.5
    candle_body = abs(close - open_) if _finite(close, open_) else float("nan")
    bearish_engulfing_breakdown = _finite(close, open_, candle_body, atr14, prev_3d_low, volume_ratio_20) and close < open_ and candle_body > atr14 and close < prev_3d_low and volume_ratio_20 >= 1.5
    locked_limit_down = _finite(daily_return, close, low) and daily_return <= -0.095 and abs(close - low) / close < 0.001
    veto_reasons: list[str] = []
    if volume_breakdown:
        veto_reasons.append("爆量長黑破位")
    if gap_down_breakdown:
        veto_reasons.append("跳空下跌破位")
    if bearish_engulfing_breakdown:
        veto_reasons.append("長黑吞噬近期漲幅")
    if confirmed_support_break:
        veto_reasons.append("放量跌破MA20與10日低點")
    if locked_limit_down:
        veto_reasons.append("鎖跌停")
    veto = bool(veto_reasons)

    # External layers: no_data/unknown unless reliable data is provided.
    # no_data is not treated as pass for watchlist eligibility.
    market_state = _trend_state_from_df(market_df)
    sector_state = _trend_state_from_df(sector_df)
    event_risk_state = str((event_data or {}).get("event_risk_state", "unknown"))
    weekly_state = "insufficient_data" if data_level == "partial" else "neutral"

    # Basic weekly confirmation from stock df when full enough.
    if n >= 250:
        try:
            weekly = ind.copy()
            weekly["date_dt"] = pd.to_datetime(weekly["date"])
            wk = weekly.set_index("date_dt").resample("W-FRI").agg({"close": "last"}).dropna()
            last_daily_date = pd.to_datetime(weekly["date_dt"].iloc[-1])
            today = pd.Timestamp.today().normalize()
            current_week_start = today - pd.Timedelta(days=today.weekday())
            # Exclude only the live unfinished week. Historical weeks that end on
            # Thursday because Friday was a holiday should remain valid weekly bars.
            if len(wk) and last_daily_date.normalize() >= current_week_start and last_daily_date.weekday() != 4:
                wk = wk.iloc[:-1]
            wk["weekly_ma20"] = wk["close"].rolling(20, min_periods=20).mean()
            if len(wk) >= 24 and pd.notna(wk["weekly_ma20"].iloc[-1]) and pd.notna(wk["weekly_ma20"].iloc[-4]):
                if wk["close"].iloc[-1] > wk["weekly_ma20"].iloc[-1] and wk["weekly_ma20"].iloc[-1] > wk["weekly_ma20"].iloc[-4]:
                    weekly_state = "bullish"
                elif wk["close"].iloc[-1] < wk["weekly_ma20"].iloc[-1] and wk["weekly_ma20"].iloc[-1] < wk["weekly_ma20"].iloc[-4]:
                    weekly_state = "bearish"
                else:
                    weekly_state = "neutral"
        except Exception:
            weekly_state = "insufficient_data"

    target_candidates: list[float] = []
    if _finite(prev_60d_high, close):
        if close > prev_60d_high:
            atr_target = close + 1.5 * atr14 if _finite(atr14) else None
            if atr_target is not None:
                target_candidates.append(atr_target)
                if _finite(boll_upper) and boll_upper > close:
                    target_candidates.append(max(atr_target, boll_upper))
            elif _finite(boll_upper) and boll_upper > close:
                target_candidates.append(boll_upper)
        elif prev_60d_high > close:
            target_candidates.append(prev_60d_high)
    target_candidates = [float(x) for x in target_candidates if _finite(float(x)) and float(x) > close]
    target_price = min(target_candidates) if target_candidates else None
    risk_reward_ratio = None
    rr_state = "unknown"
    if target_price is not None and _finite(stop_loss_candidate):
        risk = close - stop_loss_candidate
        reward = target_price - close
        if risk > 0:
            risk_reward_ratio = round(float(reward / risk), 2)
            if risk_reward_ratio >= 2.0:
                rr_state = "good"
            elif risk_reward_ratio >= 1.5:
                rr_state = "acceptable"
            elif risk_reward_ratio >= 1.0:
                rr_state = "poor"
            else:
                rr_state = "unacceptable"

    if not _finite(dif, macd):
        macd_state = "unknown"
    else:
        macd_state = "golden_cross" if macd_golden_cross else "death_cross" if macd_death_cross else "bullish_hold" if macd_bullish_hold else "bearish_hold" if macd_bearish_hold else "neutral"
    rsi_state = "cross_up_50" if rsi_cross_up_50 else "cross_down_50" if rsi_cross_down_50 else "above_50" if _finite(rsi10) and rsi10 > 50 else "below_50" if _finite(rsi10) else "unknown"
    kd_state = "golden_cross" if kd_golden_cross else "death_cross" if kd_death_cross else "bullish" if _finite(k, d) and k > d else "bearish" if _finite(k, d) else "unknown"

    market_ok = market_state != "bearish"
    sector_ok = sector_state != "bearish"
    # legacy only:
    # eligible_for_watchlist / signal / technical_total 是舊技術分數系統。
    # 主畫面正式狀態由 app.py classify_practical_status() 決定。
    # 不得用 eligible_for_watchlist 推翻 main_status。
    eligible_for_watchlist = (
        technical_total >= 70 and trend_score >= 24 and entry_score >= 20 and volume_score >= 8 and risk_score >= 5 and not veto
        and liquidity_state != "insufficient" and stop_risk_state != "poor" and market_ok and weekly_state != "bearish"
        and sector_ok and not hard_support_break and event_risk_state != "disposition_stock"
        and risk_reward_ratio is not None and risk_reward_ratio >= 1.5
    )

    blockers: list[str] = []
    warnings: list[str] = []
    if veto:
        blockers.append("危險訊號/技術否決")
    if liquidity_state == "insufficient":
        blockers.append("流動性不足")
    if liquidity_state == "volume_suspended":
        warnings.append("近期成交量異常，量能指標失效")
    if stop_risk_state == "poor":
        blockers.append("停損距離過大")
    if event_risk_state == "disposition_stock":
        blockers.append("處置股")
    if market_state == "bearish":
        warnings.append("大盤偏空")
    if sector_state == "bearish":
        warnings.append("族群偏弱")
    if hard_support_break:
        blockers.append("跌破MA20與10日低點")
    if technical_total >= 70 and entry_score < 20:
        warnings.append("不宜追價")
    if technical_total >= 70 and volume_score < 8:
        warnings.append("量價確認不足")
    if technical_total >= 70 and risk_score < 5:
        warnings.append("風險偏高")
    if weekly_state == "bearish":
        warnings.append("週線偏空")
    warnings.extend(volume_quality_warnings)
    if risk_reward_ratio is None:
        warnings.append("無法確認上方空間")
    elif risk_reward_ratio < 1.5:
        warnings.append("風險報酬不佳")

    if eligible_for_watchlist:
        signal = "可列入買進觀察"
    elif blockers:
        signal = "不可列入觀察｜" + "｜".join(blockers[:3])
    elif technical_total >= 70 and warnings:
        signal = "技術偏多｜但" + "｜".join(warnings[:3])
    else:
        signal = _general_signal(technical_total)

    sub_scores = {
        "ma_structure": ma_struct_score,
        "ma20_direction": ma20_dir_score,
        "ma60_direction": ma60_dir_score,
        "macd_trend": macd_score,
        "rsi10_entry": rsi_score,
        "ma20_distance": distance_score,
        "pattern": pattern_score,
        "kd_trigger": kd_score,
        "volume_state": vol_score,
        "obv_trend": obv_score,
        "overheat_risk": overheat_score,
        "divergence_risk": divergence_score,
        "atr_risk": atr_score,
    }
    sub_reasons = {
        "ma_structure": ma_struct_reason,
        "ma20_direction": ma20_dir_reason,
        "ma60_direction": ma60_dir_reason,
        "macd_trend": macd_reason,
        "rsi10_entry": rsi_reason,
        "ma20_distance": distance_reason,
        "pattern": pattern_reason,
        "kd_trigger": kd_reason,
        "volume_state": vol_reason,
        "obv_trend": obv_reason,
        "overheat_risk": overheat_reason,
        "divergence_risk": divergence_reason,
        "atr_risk": atr_reason,
    }
    best_key = max(sub_scores, key=sub_scores.get)
    worst_key = min(sub_scores, key=sub_scores.get)
    reasons = [
        f"最高分：{best_key} {sub_scores[best_key]}分，{sub_reasons[best_key]}",
        f"最低分：{worst_key} {sub_scores[worst_key]}分，{sub_reasons[worst_key]}",
    ]
    if veto_reasons:
        reasons.append("否決原因：" + "、".join(veto_reasons))
    if stop_risk_state == "poor":
        reasons.append("停損距離過大，不列入買進觀察")
    if blockers:
        reasons.append("阻擋原因：" + "、".join(blockers))
    if warnings:
        reasons.append("警示原因：" + "、".join(warnings))

    # Exit module remains separated and only runs when position_data is supplied.
    exit_required = False
    exit_level: int | None = None
    exit_reason: str | None = None
    reduce_position = False
    if position_data:
        stop_loss = position_data.get("stop_loss")
        if stop_loss is not None and _finite(float(stop_loss), close) and close <= float(stop_loss):
            exit_required, exit_level, exit_reason = True, 1, "跌破停損"
        elif locked_limit_down:
            exit_required, exit_level, exit_reason = True, 1, "鎖跌停，必須送出賣出委託但不保證成交"
        elif volume_breakdown:
            exit_required, exit_level, exit_reason = True, 1, "爆量長黑破位"
        elif gap_down_breakdown:
            exit_required, exit_level, exit_reason = True, 1, "跳空下跌破位"

        if not exit_required:
            ma20_breakdown = _finite(close, ma20, _last(ind["ma20"], -5)) and close < ma20 and ma20 <= _last(ind["ma20"], -5)
            macd_bearish_breakdown = macd_death_cross and dif < 0
            obv_bearish_breakdown = _finite(obv_slope_20d) and obv_slope_20d < 0
            broken = sum([bool(ma20_breakdown), bool(technical_total < 55), bool(macd_bearish_breakdown), bool(obv_bearish_breakdown)])
            if broken >= 2:
                exit_required, exit_level, exit_reason = True, 2, "趨勢破壞退出"
            if not exit_required:
                reduce_flags = sum([bool(sector_state == "bearish"), bool(macd_bearish_divergence), bool(obv_bearish_divergence), bool(technical_total < 55)])
                reduce_position = reduce_flags >= 2

    return {
        "system_version": SYSTEM_VERSION,
        "date": str(ind["date"].iloc[-1]),
        "close": round(close, 4) if _finite(close) else None,
        "data_valid": True,
        "data_level": data_level,
        "liquidity_state": liquidity_state,
        "scores": {
            "trend_score": round(float(trend_score), 2),
            "entry_score": round(float(entry_score), 2),
            "volume_score": round(float(volume_score), 2),
            "risk_score": round(float(risk_score), 2),
            "technical_total": technical_total,
        },
        "sub_scores": sub_scores,
        "states": {
            "macd_state": macd_state,
            "rsi_state": rsi_state,
            "kd_state": kd_state,
            "setup_type": setup_type,
            "stop_risk_state": stop_risk_state,
            "market_state": market_state,
            "weekly_state": weekly_state,
            "sector_state": sector_state,
            "event_risk_state": event_risk_state,
            "rr_state": rr_state,
            "support_break_state": "confirmed" if confirmed_support_break else ("hard" if hard_support_break else ("soft" if soft_support_break else "none")),
        },
        "stop_loss_candidate": round(stop_loss_candidate, 4) if _finite(stop_loss_candidate) else None,
        "target_price": round(target_price, 4) if target_price is not None else None,
        "risk_reward_ratio": risk_reward_ratio,
        "veto": veto,
        "veto_reasons": veto_reasons,
        "eligible_for_watchlist": eligible_for_watchlist,
        "blockers": blockers,
        "warnings": warnings,
        "signal": signal,
        "reasons": reasons,
        "exit_required": exit_required,
        "exit_level": exit_level,
        "exit_reason": exit_reason,
        "reduce_position": reduce_position,
        # Backward-compatible fields used by the current web UI.
        "total": technical_total,
        "reason": "；".join(reasons),
    }
