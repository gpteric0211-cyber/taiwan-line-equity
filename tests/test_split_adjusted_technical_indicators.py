from __future__ import annotations

import math
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from repository.rsi_adjustment_repository import apply_rsi_split_adjustments
from scoring import calculate_indicators


def reference_ema(values: list[float], period: int) -> list[float | None]:
    """Independent SMA-seeded EMA reference implementation."""

    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    previous = sum(values[:period]) / period
    result[period - 1] = previous
    alpha = 2.0 / (period + 1)
    for index in range(period, len(values)):
        previous = alpha * values[index] + (1.0 - alpha) * previous
        result[index] = previous
    return result


def reference_macd(values: list[float]) -> tuple[float, float, float]:
    ema12 = reference_ema(values, 12)
    ema26 = reference_ema(values, 26)
    dif: list[float | None] = [
        (left - right) if left is not None and right is not None else None
        for left, right in zip(ema12, ema26)
    ]
    valid_dif = [value for value in dif if value is not None]
    signal_valid = reference_ema(valid_dif, 9)
    signal: list[float | None] = [None] * len(values)
    valid_index = 0
    for index, value in enumerate(dif):
        if value is not None:
            signal[index] = signal_valid[valid_index]
            valid_index += 1
    final_dif = dif[-1]
    final_signal = signal[-1]
    if final_dif is None or final_signal is None:
        raise AssertionError("reference series is too short for MACD")
    return final_dif, final_signal, final_dif - final_signal


def reference_kd(
    highs: list[float], lows: list[float], closes: list[float]
) -> tuple[float, float]:
    k = 50.0
    d = 50.0
    for index in range(8, len(closes)):
        window_low = min(lows[index - 8 : index + 1])
        window_high = max(highs[index - 8 : index + 1])
        rsv = (closes[index] - window_low) / (window_high - window_low) * 100.0
        k = (2.0 * k + rsv) / 3.0
        d = (2.0 * d + k) / 3.0
    return k, d


def reference_atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> float:
    true_ranges: list[float] = []
    for index, (high, low) in enumerate(zip(highs, lows)):
        candidates = [high - low]
        if index > 0:
            candidates.extend(
                [abs(high - closes[index - 1]), abs(low - closes[index - 1])]
            )
        true_ranges.append(max(candidates))
    atr = sum(true_ranges[:period]) / period
    for true_range in true_ranges[period:]:
        atr = ((period - 1) * atr + true_range) / period
    return atr


class SplitAdjustedTechnicalIndicatorGoldenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adjusted_open: list[float] = []
        self.adjusted_high: list[float] = []
        self.adjusted_low: list[float] = []
        self.adjusted_close: list[float] = []
        rows: list[dict[str, float | int | str]] = []
        start = date(2026, 1, 2)
        split_index = 65

        for index in range(90):
            close = 80.0 + index * 0.37 + ((index % 7) - 3) * 0.21
            open_price = close + ((index % 5) - 2) * 0.13
            high = max(open_price, close) + 0.85 + (index % 3) * 0.08
            low = min(open_price, close) - 0.72 - (index % 4) * 0.06
            self.adjusted_open.append(open_price)
            self.adjusted_high.append(high)
            self.adjusted_low.append(low)
            self.adjusted_close.append(close)

            raw_multiplier = 2.0 if index < split_index else 1.0
            rows.append(
                {
                    "date": (start + timedelta(days=index)).isoformat(),
                    "open": open_price * raw_multiplier,
                    "high": high * raw_multiplier,
                    "low": low * raw_multiplier,
                    "close": close * raw_multiplier,
                    "volume": 1_000_000 + index * 1_337,
                }
            )

        split_date = str(rows[split_index]["date"])
        self.raw_rows = rows
        self.rows = apply_rsi_split_adjustments(
            rows,
            [{"event_date": split_date, "pre_event_factor": 0.5}],
        )
        self.indicators = calculate_indicators(pd.DataFrame(self.rows))

    def test_raw_ohlc_is_unchanged_while_technical_ohlc_is_split_adjusted(self) -> None:
        for index, (raw, adjusted) in enumerate(zip(self.raw_rows, self.rows)):
            for field, reference_values in (
                ("open", self.adjusted_open),
                ("high", self.adjusted_high),
                ("low", self.adjusted_low),
                ("close", self.adjusted_close),
            ):
                self.assertEqual(adjusted[field], raw[field])
                self.assertAlmostEqual(
                    adjusted[f"technical_{field}"], reference_values[index], places=12
                )
            self.assertEqual(adjusted["rsi_close"], adjusted["technical_close"])

        self.assertEqual(
            self.indicators.iloc[0]["close"], self.raw_rows[0]["close"]
        )
        self.assertEqual(
            self.indicators.iloc[-1]["close"], self.raw_rows[-1]["close"]
        )

    def test_ma_and_macd_match_independent_reference(self) -> None:
        latest = self.indicators.iloc[-1]
        expected_ma20 = sum(self.adjusted_close[-20:]) / 20.0
        expected_dif, expected_signal, expected_osc = reference_macd(
            self.adjusted_close
        )

        self.assertAlmostEqual(latest["ma20"], expected_ma20, places=12)
        self.assertAlmostEqual(latest["dif"], expected_dif, places=12)
        self.assertAlmostEqual(latest["macd_signal"], expected_signal, places=12)
        self.assertAlmostEqual(latest["osc"], expected_osc, places=12)

    def test_kd_and_atr_match_independent_reference(self) -> None:
        latest = self.indicators.iloc[-1]
        expected_k, expected_d = reference_kd(
            self.adjusted_high, self.adjusted_low, self.adjusted_close
        )
        expected_atr = reference_atr(
            self.adjusted_high, self.adjusted_low, self.adjusted_close
        )

        self.assertAlmostEqual(latest["k"], expected_k, places=12)
        self.assertAlmostEqual(latest["d"], expected_d, places=12)
        self.assertAlmostEqual(latest["atr14"], expected_atr, places=12)

    def test_bollinger_obv_and_levels_use_adjusted_prices(self) -> None:
        latest = self.indicators.iloc[-1]
        last_twenty = self.adjusted_close[-20:]
        mean = sum(last_twenty) / 20.0
        sigma = math.sqrt(sum((value - mean) ** 2 for value in last_twenty) / 20.0)
        expected_obv = 0.0
        volumes = [float(row["volume"]) for row in self.raw_rows]
        for index in range(1, len(self.adjusted_close)):
            direction = 1.0 if self.adjusted_close[index] > self.adjusted_close[index - 1] else -1.0
            if self.adjusted_close[index] == self.adjusted_close[index - 1]:
                direction = 0.0
            expected_obv += direction * volumes[index]

        self.assertAlmostEqual(latest["boll_upper"], mean + 2.0 * sigma, places=12)
        self.assertAlmostEqual(latest["boll_lower"], mean - 2.0 * sigma, places=12)
        self.assertAlmostEqual(latest["obv"], expected_obv, places=12)
        self.assertAlmostEqual(
            latest["previous_10d_low"], min(self.adjusted_low[-11:-1]), places=12
        )
        self.assertAlmostEqual(
            latest["previous_20d_high"], max(self.adjusted_high[-21:-1]), places=12
        )


if __name__ == "__main__":
    unittest.main()
