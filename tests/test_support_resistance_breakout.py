from __future__ import annotations

import sys
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from analysis.support_resistance import (  # noqa: E402
    SUPPORT_RESISTANCE_ASSEMBLER_VERSION,
    build_ohlcv_support_resistance_levels,
    cluster_levels,
)


def test_latest_completed_high_supplies_weak_resistance_after_breakout() -> None:
    rows = [
        {
            "date": f"2026-06-{day:02d}",
            "open": 6190.0,
            "high": 6300.0,
            "low": 6100.0,
            "close": 6200.0,
            "volume": 10_000.0 if day in {1, 2} else 1_000.0,
        }
        for day in range(1, 21)
    ]
    rows[-2] = {
        "date": "2026-08-27",
        "open": 6770.0,
        "high": 6960.0,
        "low": 6735.0,
        "close": 6815.0,
        "volume": 900.0,
    }
    rows[-1] = {
        "date": "2026-08-28",
        "open": 6965.0,
        "high": 7245.0,
        "low": 6835.0,
        "close": 7200.0,
        "volume": 800.0,
    }

    levels = build_ohlcv_support_resistance_levels(rows, current=7200.0)
    resistances = cluster_levels(
        [level for level in levels if level.get("side") == "resistance"],
        current=7200.0,
    )

    assert SUPPORT_RESISTANCE_ASSEMBLER_VERSION == "dashboard-ohlcv-support-resistance-v2"
    assert resistances
    assert resistances[0]["zone_low"] == 7245.0
    assert resistances[0]["zone_high"] == 7245.0
    assert resistances[0]["strength"] == "弱"
    assert any(
        raw["source"] == "最近交易日高點"
        and raw["status"] == "latest_session_high"
        for raw in resistances[0]["raw_levels"]
    )
