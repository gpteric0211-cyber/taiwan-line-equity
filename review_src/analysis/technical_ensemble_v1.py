from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


TECHNICAL_ENSEMBLE_VERSION = "TechnicalEnsembleV1"
TECHNICAL_FORMULA_VERSION_V1 = "TechnicalFormulaV1-candidate.1"
TECHNICAL_SCORE_VERSION_V1 = "TechnicalEnsembleScoreV1-candidate.1"
TECHNICAL_MINIMUM_HISTORY_ROWS = 240


COMPONENT_SPECS: tuple[dict[str, Any], ...] = (
    {"indicator_key": "trend", "component_key": "ma5", "column": "ma5", "unit": "TWD", "parameters": {"period": 5, "method": "sma"}, "warmup": 5},
    {"indicator_key": "trend", "component_key": "ma10", "column": "ma10", "unit": "TWD", "parameters": {"period": 10, "method": "sma"}, "warmup": 10},
    {"indicator_key": "trend", "component_key": "ma20", "column": "ma20", "unit": "TWD", "parameters": {"period": 20, "method": "sma"}, "warmup": 20},
    {"indicator_key": "trend", "component_key": "ma60", "column": "ma60", "unit": "TWD", "parameters": {"period": 60, "method": "sma"}, "warmup": 60},
    {"indicator_key": "trend", "component_key": "ma120", "column": "ma120", "unit": "TWD", "parameters": {"period": 120, "method": "sma"}, "warmup": 120},
    {"indicator_key": "trend", "component_key": "ma240", "column": "ma240", "unit": "TWD", "parameters": {"period": 240, "method": "sma"}, "warmup": 240},
    {"indicator_key": "trend", "component_key": "ema12", "column": "ema12", "unit": "TWD", "parameters": {"period": 12, "alpha": "2/(n+1)", "seed": "sma"}, "warmup": 12},
    {"indicator_key": "trend", "component_key": "ema26", "column": "ema26", "unit": "TWD", "parameters": {"period": 26, "alpha": "2/(n+1)", "seed": "sma"}, "warmup": 26},
    {"indicator_key": "trend", "component_key": "macd_dif", "column": "macd_dif", "unit": "TWD", "parameters": {"fast": 12, "slow": 26}, "warmup": 26},
    {"indicator_key": "trend", "component_key": "macd_signal", "column": "macd_signal", "unit": "TWD", "parameters": {"period": 9, "seed": "sma_of_dif"}, "warmup": 34},
    {"indicator_key": "trend", "component_key": "macd_histogram", "column": "macd_histogram", "unit": "TWD", "parameters": {"definition": "dif-signal"}, "warmup": 34},
    {"indicator_key": "trend", "component_key": "macd_zero_axis", "column": "macd_zero_axis", "unit": "direction", "parameters": {"definition": "sign(dif)"}, "warmup": 26},
    {"indicator_key": "trend", "component_key": "macd_cross_age", "column": "macd_cross_age", "unit": "trading_days", "parameters": {"definition": "days_since_dif_signal_cross"}, "warmup": 35},
    {"indicator_key": "trend", "component_key": "macd_cross_date", "column": "macd_cross_date", "unit": "trade_date", "parameters": {"definition": "latest_dif_signal_cross_date"}, "warmup": 35, "value_kind": "text"},
    {"indicator_key": "trend", "component_key": "ma20_ma60_spread", "column": "ma20_ma60_spread", "unit": "percent", "parameters": {"definition": "100*(ma20/ma60-1)"}, "warmup": 60},
    {"indicator_key": "trend", "component_key": "plus_di14", "column": "plus_di14", "unit": "index", "parameters": {"period": 14, "method": "wilder"}, "warmup": 14},
    {"indicator_key": "trend", "component_key": "minus_di14", "column": "minus_di14", "unit": "index", "parameters": {"period": 14, "method": "wilder"}, "warmup": 14},
    {"indicator_key": "trend", "component_key": "adx14", "column": "adx14", "unit": "index", "parameters": {"period": 14, "method": "wilder"}, "warmup": 27},
    {"indicator_key": "momentum", "component_key": "rsi5", "column": "rsi5", "unit": "index", "parameters": {"period": 5, "method": "wilder"}, "warmup": 6},
    {"indicator_key": "momentum", "component_key": "rsi10", "column": "rsi10", "unit": "index", "parameters": {"period": 10, "method": "wilder"}, "warmup": 11},
    {"indicator_key": "momentum", "component_key": "rsi14", "column": "rsi14", "unit": "index", "parameters": {"period": 14, "method": "wilder"}, "warmup": 15},
    {"indicator_key": "momentum", "component_key": "kd_k", "column": "kd_k", "unit": "index", "parameters": {"period": 9, "seed": 50, "alpha": "1/3"}, "warmup": 9},
    {"indicator_key": "momentum", "component_key": "kd_d", "column": "kd_d", "unit": "index", "parameters": {"period": 9, "seed": 50, "alpha": "1/3"}, "warmup": 9},
    {"indicator_key": "momentum", "component_key": "kd_j", "column": "kd_j", "unit": "index", "parameters": {"definition": "3K-2D", "family_vote": "KD_KDJ_one_vote"}, "warmup": 9},
    {"indicator_key": "momentum", "component_key": "williams_r14", "column": "williams_r14", "unit": "index", "parameters": {"period": 14}, "warmup": 14},
    {"indicator_key": "momentum", "component_key": "cci20", "column": "cci20", "unit": "index", "parameters": {"period": 20, "constant": 0.015}, "warmup": 20},
    {"indicator_key": "momentum", "component_key": "mtm10", "column": "mtm10", "unit": "TWD", "parameters": {"period": 10}, "warmup": 11},
    {"indicator_key": "momentum", "component_key": "roc10", "column": "roc10", "unit": "percent", "parameters": {"period": 10}, "warmup": 11},
    {"indicator_key": "momentum", "component_key": "bias6", "column": "bias6", "unit": "percent", "parameters": {"period": 6}, "warmup": 6},
    {"indicator_key": "momentum", "component_key": "bias12", "column": "bias12", "unit": "percent", "parameters": {"period": 12}, "warmup": 12},
    {"indicator_key": "momentum", "component_key": "bias24", "column": "bias24", "unit": "percent", "parameters": {"period": 24}, "warmup": 24},
    {"indicator_key": "volume", "component_key": "volume", "column": "volume", "unit": "shares", "parameters": {"period": 1}, "warmup": 1},
    {"indicator_key": "volume", "component_key": "volume_ma5", "column": "volume_ma5", "unit": "shares", "parameters": {"period": 5, "method": "sma"}, "warmup": 5},
    {"indicator_key": "volume", "component_key": "volume_ma20", "column": "volume_ma20", "unit": "shares", "parameters": {"period": 20, "method": "sma"}, "warmup": 20},
    {"indicator_key": "volume", "component_key": "volume_ma60", "column": "volume_ma60", "unit": "shares", "parameters": {"period": 60, "method": "sma"}, "warmup": 60},
    {"indicator_key": "volume", "component_key": "obv", "column": "obv", "unit": "shares", "parameters": {"seed": 0, "state": "continuous"}, "warmup": 2},
    {"indicator_key": "volume", "component_key": "obv_delta20", "column": "obv_delta20", "unit": "shares", "parameters": {"period": 20, "basis": "continuous_state"}, "warmup": 21},
    {"indicator_key": "volume", "component_key": "ad", "column": "ad", "unit": "shares", "parameters": {"seed": 0, "method": "chaikin", "state": "continuous"}, "warmup": 1},
    {"indicator_key": "volume", "component_key": "ad_delta20", "column": "ad_delta20", "unit": "shares", "parameters": {"period": 20, "basis": "continuous_state"}, "warmup": 21},
    {"indicator_key": "volume", "component_key": "vr26", "column": "vr26", "unit": "index", "parameters": {"period": 26, "flat_weight": 0.5}, "warmup": 26},
    {"indicator_key": "volume", "component_key": "eom14", "column": "eom14", "unit": "TWD2_per_share", "parameters": {"period": 14, "method": "sma"}, "warmup": 15},
    {"indicator_key": "volume", "component_key": "nvi", "column": "nvi", "unit": "index", "parameters": {"seed": 1000, "state": "continuous"}, "warmup": 2},
    {"indicator_key": "volume", "component_key": "nvi_return20", "column": "nvi_return20", "unit": "percent", "parameters": {"period": 20, "basis": "continuous_state"}, "warmup": 21},
    {"indicator_key": "volume", "component_key": "pvi", "column": "pvi", "unit": "index", "parameters": {"seed": 1000, "state": "continuous"}, "warmup": 2},
    {"indicator_key": "volume", "component_key": "pvi_return20", "column": "pvi_return20", "unit": "percent", "parameters": {"period": 20, "basis": "continuous_state"}, "warmup": 21},
    {"indicator_key": "volume", "component_key": "vao14", "column": "vao14", "unit": "TWD_shares", "parameters": {"period": 14, "method": "rolling_sum"}, "warmup": 14},
    {"indicator_key": "volatility", "component_key": "bollinger_mid20", "column": "bollinger_mid20", "unit": "TWD", "parameters": {"period": 20}, "warmup": 20},
    {"indicator_key": "volatility", "component_key": "bollinger_upper20", "column": "bollinger_upper20", "unit": "TWD", "parameters": {"period": 20, "stddev": 2, "ddof": 0}, "warmup": 20},
    {"indicator_key": "volatility", "component_key": "bollinger_lower20", "column": "bollinger_lower20", "unit": "TWD", "parameters": {"period": 20, "stddev": 2, "ddof": 0}, "warmup": 20},
    {"indicator_key": "volatility", "component_key": "bollinger_width20", "column": "bollinger_width20", "unit": "percent", "parameters": {"period": 20}, "warmup": 20},
    {"indicator_key": "volatility", "component_key": "atr14", "column": "atr14", "unit": "TWD", "parameters": {"period": 14, "method": "wilder"}, "warmup": 14},
    {"indicator_key": "volatility", "component_key": "weighted_close", "column": "weighted_close", "unit": "TWD", "parameters": {"definition": "(H+L+2C)/4"}, "warmup": 1},
    {"indicator_key": "psychology", "component_key": "psy12", "column": "psy12", "unit": "index", "parameters": {"period": 12}, "warmup": 13},
    {"indicator_key": "psychology", "component_key": "ar26", "column": "ar26", "unit": "index", "parameters": {"period": 26}, "warmup": 26},
    {"indicator_key": "psychology", "component_key": "br26", "column": "br26", "unit": "index", "parameters": {"period": 26}, "warmup": 27},
    {"indicator_key": "ensemble", "component_key": "trend_score", "column": "trend_score", "unit": "score_-100_100", "parameters": {"family_weight": 0.30, "score_version": TECHNICAL_SCORE_VERSION_V1}, "warmup": 60},
    {"indicator_key": "ensemble", "component_key": "momentum_score", "column": "momentum_score", "unit": "score_-100_100", "parameters": {"family_weight": 0.25, "score_version": TECHNICAL_SCORE_VERSION_V1}, "warmup": 24},
    {"indicator_key": "ensemble", "component_key": "volume_score", "column": "volume_score", "unit": "score_-100_100", "parameters": {"family_weight": 0.25, "score_version": TECHNICAL_SCORE_VERSION_V1}, "warmup": 60},
    {"indicator_key": "ensemble", "component_key": "volatility_score", "column": "volatility_score", "unit": "score_-100_100", "parameters": {"family_weight": 0.10, "score_version": TECHNICAL_SCORE_VERSION_V1}, "warmup": 20},
    {"indicator_key": "ensemble", "component_key": "psychology_score", "column": "psychology_score", "unit": "score_-100_100", "parameters": {"family_weight": 0.10, "score_version": TECHNICAL_SCORE_VERSION_V1}, "warmup": 27},
    {"indicator_key": "ensemble", "component_key": "ensemble_score", "column": "ensemble_score", "unit": "score_-100_100", "parameters": {"weights": {"trend": 0.30, "momentum": 0.25, "volume": 0.25, "volatility": 0.10, "psychology": 0.10}, "missing_weight_redistribution": False, "score_version": TECHNICAL_SCORE_VERSION_V1}, "warmup": 240},
    {"indicator_key": "ensemble", "component_key": "ensemble_coverage", "column": "ensemble_coverage", "unit": "ratio", "parameters": {"definition": "available_family_weight"}, "warmup": 27},
)


def _seeded_ema(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    if len(values) < period or not np.isfinite(values[:period]).all():
        return out
    out[period - 1] = float(np.mean(values[:period]))
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        if np.isfinite(values[index]) and np.isfinite(out[index - 1]):
            out[index] = alpha * values[index] + (1.0 - alpha) * out[index - 1]
    return out


def _wilder_average(values: np.ndarray, period: int, *, start: int = 0) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    end = start + period
    if len(values) < end or not np.isfinite(values[start:end]).all():
        return out
    seed_index = end - 1
    out[seed_index] = float(np.mean(values[start:end]))
    for index in range(seed_index + 1, len(values)):
        if np.isfinite(values[index]) and np.isfinite(out[index - 1]):
            out[index] = ((period - 1.0) * out[index - 1] + values[index]) / period
    return out


def _safe_divide(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    return np.divide(
        numerator,
        denominator,
        out=np.full(len(numerator), np.nan, dtype=float),
        where=np.isfinite(numerator) & np.isfinite(denominator) & (denominator != 0),
    )


def _rsi(close: np.ndarray, period: int) -> np.ndarray:
    delta = np.diff(close, prepend=np.nan)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    gain[0] = np.nan
    loss[0] = np.nan
    average_gain = _wilder_average(gain, period, start=1)
    average_loss = _wilder_average(loss, period, start=1)
    out = np.full(len(close), np.nan, dtype=float)
    both_zero = (average_gain == 0) & (average_loss == 0)
    only_loss_zero = (average_gain > 0) & (average_loss == 0)
    regular = (average_gain >= 0) & (average_loss > 0)
    out[both_zero] = 50.0
    out[only_loss_zero] = 100.0
    rs = np.full(len(close), np.nan, dtype=float)
    rs[regular] = average_gain[regular] / average_loss[regular]
    out[regular] = 100.0 - 100.0 / (1.0 + rs[regular])
    return out


def _rolling_mean_deviation(values: pd.Series, period: int) -> pd.Series:
    return values.rolling(period, min_periods=period).apply(
        lambda window: float(np.mean(np.abs(window - np.mean(window)))),
        raw=True,
    )


def _bounded_mean(signals: Iterable[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    matrix = np.vstack(list(signals)).T
    available = np.isfinite(matrix)
    coverage = available.mean(axis=1)
    totals = np.nansum(matrix, axis=1)
    counts = available.sum(axis=1)
    mean = np.divide(
        totals,
        counts,
        out=np.full(len(matrix), np.nan, dtype=float),
        where=counts > 0,
    )
    return np.clip(mean, -1.0, 1.0) * 100.0, coverage


def _tanh_ratio(numerator: np.ndarray, denominator: np.ndarray, scale: float = 1.0) -> np.ndarray:
    ratio = _safe_divide(numerator, denominator * scale)
    return np.tanh(ratio)


def compute_technical_ensemble_frame(rows: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    """Compute additive V1 components from ascending, validated daily OHLCV."""

    source_rows = [dict(row) for row in rows]
    if not source_rows:
        return pd.DataFrame()
    dates = pd.Series([str(row.get("date") or "") for row in source_rows], dtype="object")

    def numeric(field: str) -> np.ndarray:
        adjusted = f"technical_{field}"
        values = [
            row.get(adjusted) if row.get(adjusted) is not None else row.get(field)
            for row in source_rows
        ]
        return pd.to_numeric(pd.Series(values), errors="coerce").to_numpy(dtype=float)

    open_ = numeric("open")
    high = numeric("high")
    low = numeric("low")
    close = numeric("close")
    volume = pd.to_numeric(
        pd.Series([row.get("volume") for row in source_rows]), errors="coerce"
    ).to_numpy(dtype=float)
    frame = pd.DataFrame({"trade_date": dates, "volume": volume})
    close_series = pd.Series(close)
    high_series = pd.Series(high)
    low_series = pd.Series(low)
    open_series = pd.Series(open_)
    volume_series = pd.Series(volume)

    for period in (5, 10, 20, 60, 120, 240):
        frame[f"ma{period}"] = close_series.rolling(period, min_periods=period).mean()
    ema12 = _seeded_ema(close, 12)
    ema26 = _seeded_ema(close, 26)
    dif = ema12 - ema26
    signal = np.full(len(close), np.nan, dtype=float)
    valid_dif = np.flatnonzero(np.isfinite(dif))
    if len(valid_dif) >= 9:
        first = int(valid_dif[0])
        seeded = _seeded_ema(dif[first:], 9)
        signal[first:] = seeded
    histogram = dif - signal
    frame["ema12"] = ema12
    frame["ema26"] = ema26
    frame["macd_dif"] = dif
    frame["macd_signal"] = signal
    frame["macd_histogram"] = histogram
    frame["macd_zero_axis"] = np.where(np.isfinite(dif), np.sign(dif), np.nan)

    cross_dates: list[str | None] = [None] * len(close)
    cross_ages = np.full(len(close), np.nan, dtype=float)
    previous_sign = 0.0
    latest_cross: str | None = None
    age: int | None = None
    for index, spread in enumerate(histogram):
        if not np.isfinite(spread) or spread == 0:
            if latest_cross is not None:
                cross_dates[index] = latest_cross
                cross_ages[index] = float(age or 0)
            continue
        current_sign = float(np.sign(spread))
        if previous_sign and current_sign != previous_sign:
            latest_cross = str(dates.iloc[index])
            age = 0
        elif latest_cross is not None:
            age = int(age or 0) + 1
        previous_sign = current_sign
        cross_dates[index] = latest_cross
        if age is not None:
            cross_ages[index] = float(age)
    frame["macd_cross_date"] = cross_dates
    frame["macd_cross_age"] = cross_ages
    frame["ma20_ma60_spread"] = 100.0 * (
        _safe_divide(frame["ma20"].to_numpy(), frame["ma60"].to_numpy()) - 1.0
    )

    previous_close = np.roll(close, 1)
    previous_close[0] = np.nan
    tr = np.nanmax(
        np.vstack((high - low, np.abs(high - previous_close), np.abs(low - previous_close))),
        axis=0,
    )
    up_move = high - np.roll(high, 1)
    down_move = np.roll(low, 1) - low
    up_move[0] = np.nan
    down_move[0] = np.nan
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm[0] = 0.0
    minus_dm[0] = 0.0
    atr14 = _wilder_average(tr, 14)
    plus_di = 100.0 * _safe_divide(_wilder_average(plus_dm, 14), atr14)
    minus_di = 100.0 * _safe_divide(_wilder_average(minus_dm, 14), atr14)
    dx = 100.0 * _safe_divide(np.abs(plus_di - minus_di), plus_di + minus_di)
    adx = _wilder_average(dx, 14, start=13)
    frame["plus_di14"] = plus_di
    frame["minus_di14"] = minus_di
    frame["adx14"] = adx

    for period in (5, 10, 14):
        frame[f"rsi{period}"] = _rsi(close, period)
    hh9 = high_series.rolling(9, min_periods=9).max().to_numpy()
    ll9 = low_series.rolling(9, min_periods=9).min().to_numpy()
    rsv = 100.0 * _safe_divide(close - ll9, hh9 - ll9)
    kd_k = np.full(len(close), np.nan, dtype=float)
    kd_d = np.full(len(close), np.nan, dtype=float)
    previous_k = 50.0
    previous_d = 50.0
    for index, value in enumerate(rsv):
        if not np.isfinite(value):
            continue
        previous_k = (2.0 * previous_k + value) / 3.0
        previous_d = (2.0 * previous_d + previous_k) / 3.0
        kd_k[index] = previous_k
        kd_d[index] = previous_d
    frame["kd_k"] = kd_k
    frame["kd_d"] = kd_d
    frame["kd_j"] = 3.0 * kd_k - 2.0 * kd_d
    hh14 = high_series.rolling(14, min_periods=14).max().to_numpy()
    ll14 = low_series.rolling(14, min_periods=14).min().to_numpy()
    frame["williams_r14"] = -100.0 * _safe_divide(hh14 - close, hh14 - ll14)
    typical = (high_series + low_series + close_series) / 3.0
    typical_ma20 = typical.rolling(20, min_periods=20).mean()
    mean_deviation20 = _rolling_mean_deviation(typical, 20)
    frame["cci20"] = (typical - typical_ma20) / (0.015 * mean_deviation20.replace(0, np.nan))
    frame["mtm10"] = close_series - close_series.shift(10)
    frame["roc10"] = 100.0 * (close_series / close_series.shift(10) - 1.0)
    for period in (6, 12, 24):
        average = close_series.rolling(period, min_periods=period).mean()
        frame[f"bias{period}"] = 100.0 * (close_series / average - 1.0)

    frame["volume_ma5"] = volume_series.rolling(5, min_periods=5).mean()
    frame["volume_ma20"] = volume_series.rolling(20, min_periods=20).mean()
    frame["volume_ma60"] = volume_series.rolling(60, min_periods=60).mean()
    obv = np.zeros(len(close), dtype=float)
    for index in range(1, len(close)):
        change = np.sign(close[index] - close[index - 1]) if np.isfinite(close[index:index + 1]).all() and np.isfinite(close[index - 1]) else 0.0
        obv[index] = obv[index - 1] + change * volume[index]
    frame["obv"] = obv
    frame["obv_delta20"] = pd.Series(obv) - pd.Series(obv).shift(20)
    close_location = _safe_divide((close - low) - (high - close), high - low)
    ad = np.cumsum(np.where(np.isfinite(close_location * volume), close_location * volume, 0.0))
    frame["ad"] = ad
    frame["ad_delta20"] = pd.Series(ad) - pd.Series(ad).shift(20)
    delta = close_series.diff()
    up_volume = volume_series.where(delta > 0, 0.0)
    down_volume = volume_series.where(delta < 0, 0.0)
    flat_volume = volume_series.where(delta == 0, 0.0)
    vr_numerator = up_volume.rolling(26, min_periods=26).sum() + 0.5 * flat_volume.rolling(26, min_periods=26).sum()
    vr_denominator = down_volume.rolling(26, min_periods=26).sum() + 0.5 * flat_volume.rolling(26, min_periods=26).sum()
    frame["vr26"] = 100.0 * vr_numerator / vr_denominator.replace(0, np.nan)
    midpoint_move = ((high_series + low_series) / 2.0).diff()
    eom_raw = midpoint_move * (high_series - low_series) / volume_series.replace(0, np.nan)
    frame["eom14"] = eom_raw.rolling(14, min_periods=14).mean()
    nvi = np.full(len(close), np.nan, dtype=float)
    pvi = np.full(len(close), np.nan, dtype=float)
    nvi[0] = 1000.0
    pvi[0] = 1000.0
    for index in range(1, len(close)):
        price_return = close[index] / close[index - 1] - 1.0 if close[index - 1] else 0.0
        nvi[index] = nvi[index - 1] * (1.0 + price_return) if volume[index] < volume[index - 1] else nvi[index - 1]
        pvi[index] = pvi[index - 1] * (1.0 + price_return) if volume[index] > volume[index - 1] else pvi[index - 1]
    frame["nvi"] = nvi
    frame["nvi_return20"] = 100.0 * (pd.Series(nvi) / pd.Series(nvi).shift(20) - 1.0)
    frame["pvi"] = pvi
    frame["pvi_return20"] = 100.0 * (pd.Series(pvi) / pd.Series(pvi).shift(20) - 1.0)
    vao = volume * (close - (high + low) / 2.0)
    frame["vao14"] = pd.Series(vao).rolling(14, min_periods=14).sum()

    boll_mid = close_series.rolling(20, min_periods=20).mean()
    boll_std = close_series.rolling(20, min_periods=20).std(ddof=0)
    boll_upper = boll_mid + 2.0 * boll_std
    boll_lower = boll_mid - 2.0 * boll_std
    frame["bollinger_mid20"] = boll_mid
    frame["bollinger_upper20"] = boll_upper
    frame["bollinger_lower20"] = boll_lower
    frame["bollinger_width20"] = 100.0 * (boll_upper - boll_lower) / boll_mid.replace(0, np.nan)
    frame["atr14"] = atr14
    frame["weighted_close"] = (high + low + 2.0 * close) / 4.0

    up_day = (close_series.diff() > 0).astype(float)
    frame["psy12"] = 100.0 * up_day.rolling(12, min_periods=12).sum() / 12.0
    ar_numerator = (high_series - open_series).rolling(26, min_periods=26).sum()
    ar_denominator = (open_series - low_series).rolling(26, min_periods=26).sum()
    frame["ar26"] = 100.0 * ar_numerator / ar_denominator.replace(0, np.nan)
    br_numerator = pd.Series(np.maximum(0.0, high - previous_close)).rolling(26, min_periods=26).sum()
    br_denominator = pd.Series(np.maximum(0.0, previous_close - low)).rolling(26, min_periods=26).sum()
    frame["br26"] = 100.0 * br_numerator / br_denominator.replace(0, np.nan)

    atr_scale = np.where(np.isfinite(atr14) & (atr14 > 0), atr14, np.nan)
    dmi_direction = np.where(
        np.isfinite(adx),
        np.sign(plus_di - minus_di) * np.minimum(adx / 50.0, 1.0),
        np.nan,
    )
    trend_score, trend_coverage = _bounded_mean(
        (
            _tanh_ratio(close - frame["ma20"].to_numpy(), atr_scale, 2.0),
            _tanh_ratio(frame["ma20"].to_numpy() - frame["ma60"].to_numpy(), atr_scale, 2.0),
            _tanh_ratio(ema12 - ema26, atr_scale, 1.5),
            _tanh_ratio(dif, atr_scale, 1.5),
            _tanh_ratio(histogram, atr_scale, 0.75),
            dmi_direction,
        )
    )
    momentum_score, momentum_coverage = _bounded_mean(
        (
            np.clip((frame["rsi14"].to_numpy() - 50.0) / 25.0, -1.0, 1.0),
            np.clip((kd_k - kd_d) / 20.0, -1.0, 1.0),
            np.clip((frame["williams_r14"].to_numpy() + 50.0) / 50.0, -1.0, 1.0),
            np.clip(frame["cci20"].to_numpy() / 100.0, -1.0, 1.0),
            _tanh_ratio(frame["mtm10"].to_numpy(), atr_scale, 3.0),
            np.clip(frame["roc10"].to_numpy() / 10.0, -1.0, 1.0),
            np.clip(frame["bias24"].to_numpy() / 8.0, -1.0, 1.0),
        )
    )
    average_volume20 = frame["volume_ma20"].to_numpy()
    volume_score, volume_coverage = _bounded_mean(
        (
            _tanh_ratio(frame["obv_delta20"].to_numpy(), average_volume20, 10.0),
            _tanh_ratio(frame["ad_delta20"].to_numpy(), average_volume20, 10.0),
            np.tanh(np.log(np.where(frame["vr26"].to_numpy() > 0, frame["vr26"].to_numpy() / 100.0, np.nan))),
            np.sign(frame["eom14"].to_numpy()),
            np.clip(frame["nvi_return20"].to_numpy() / 10.0, -1.0, 1.0),
            np.clip(frame["pvi_return20"].to_numpy() / 10.0, -1.0, 1.0),
            np.sign(frame["vao14"].to_numpy()),
        )
    )
    band_half_width = boll_upper.to_numpy() - boll_mid.to_numpy()
    volatility_score, volatility_coverage = _bounded_mean(
        (
            _safe_divide(close - boll_mid.to_numpy(), band_half_width),
            _tanh_ratio(close - frame["weighted_close"].to_numpy(), atr_scale, 0.5),
        )
    )
    psychology_score, psychology_coverage = _bounded_mean(
        (
            np.clip((frame["psy12"].to_numpy() - 50.0) / 25.0, -1.0, 1.0),
            np.tanh(np.log(np.where(frame["ar26"].to_numpy() > 0, frame["ar26"].to_numpy() / 100.0, np.nan))),
            np.tanh(np.log(np.where(frame["br26"].to_numpy() > 0, frame["br26"].to_numpy() / 100.0, np.nan))),
        )
    )
    frame["trend_score"] = trend_score
    frame["momentum_score"] = momentum_score
    frame["volume_score"] = volume_score
    frame["volatility_score"] = volatility_score
    frame["psychology_score"] = psychology_score
    family_scores = np.vstack(
        (trend_score, momentum_score, volume_score, volatility_score, psychology_score)
    ).T
    family_available = np.isfinite(family_scores)
    weights = np.array([0.30, 0.25, 0.25, 0.10, 0.10])
    ensemble_coverage = np.sum(family_available * weights, axis=1)
    ensemble_score = np.sum(np.nan_to_num(family_scores, nan=0.0) * weights, axis=1)
    ensemble_score[ensemble_coverage < 1.0 - 1e-12] = np.nan
    frame["ensemble_coverage"] = ensemble_coverage
    frame["ensemble_score"] = ensemble_score
    frame["trend_coverage"] = trend_coverage
    frame["momentum_coverage"] = momentum_coverage
    frame["volume_coverage"] = volume_coverage
    frame["volatility_coverage"] = volatility_coverage
    frame["psychology_coverage"] = psychology_coverage
    return frame.replace([np.inf, -np.inf], np.nan)


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
