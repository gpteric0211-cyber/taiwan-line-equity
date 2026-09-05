from __future__ import annotations

import sys
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.tdcc_equity_concentration_service import (
    compute_equity_concentration_from_distribution,
    rebuild_tdcc_equity_summaries_from_persisted_distribution,
)  # noqa: E402


def _row(level: int, lower: int, upper: int | None, percent: float) -> dict:
    return {
        "date": "2026-08-21",
        "code": "2454",
        "level": str(level),
        "lower_shares": lower,
        "upper_shares": upper,
        "holders": 1,
        "shares": 1,
        "percent": percent,
        "source": "TDCC_OPEN_DATA_1_5",
    }


def test_open_ended_highest_band_is_not_counted_as_small_holder() -> None:
    rows = [
        _row(1, 1, 999, 1.0),
        _row(2, 1000, 5000, 2.0),
        _row(3, 5001, 10000, 3.0),
        _row(4, 10001, 15000, 4.0),
        _row(5, 15001, 20000, 5.0),
        _row(6, 20001, 30000, 6.0),
        _row(7, 30001, 40000, 7.0),
        _row(8, 40001, 50000, 8.0),
        _row(9, 50001, 100000, 9.0),
        _row(10, 100001, 200000, 10.0),
        _row(11, 200001, 400000, 11.0),
        _row(12, 400001, 600000, 12.0),
        _row(13, 600001, 800000, 13.0),
        _row(14, 800001, 1000000, 14.0),
        _row(15, 1000001, None, 15.0),
    ]

    result = compute_equity_concentration_from_distribution(rows)

    assert result["quality"] == "ok"
    assert result["small_10_share_pct"] == 6.0
    assert result["big_400_share_pct"] == 54.0
    assert result["big_1000_share_pct"] == 15.0


def test_rebuild_recalculates_all_dates_and_four_week_change() -> None:
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE tdcc_holding_distribution(
            date TEXT, code TEXT, level TEXT, holders INTEGER, shares REAL,
            percent REAL, source TEXT, updated_at REAL,
            PRIMARY KEY(date,code,level)
        );
        CREATE TABLE tdcc_equity_summary(
            date TEXT, code TEXT, total_holders INTEGER, total_shares REAL,
            small_10_share_pct REAL, big_400_share_pct REAL,
            big_1000_share_pct REAL, small_10_change_4w REAL,
            big_400_change_4w REAL, big_1000_change_4w REAL,
            holder_count_change_4w REAL, holder_count_change_4w_pct REAL,
            equity_score REAL, equity_label TEXT, equity_reason TEXT,
            source TEXT, quality TEXT, updated_at REAL,
            PRIMARY KEY(date,code)
        );
        """
    )
    dates = ["2026-07-24", "2026-07-31", "2026-08-07", "2026-08-14", "2026-08-21"]
    for date_index, trade_date in enumerate(dates):
        for level in range(1, 16):
            conn.execute(
                """
                INSERT INTO tdcc_holding_distribution(
                    date,code,level,holders,shares,percent,source,updated_at
                ) VALUES(?,?,?,?,?,?,?,0)
                """,
                (
                    trade_date,
                    "2454",
                    str(level),
                    1,
                    1,
                    float(level + (date_index if level == 15 else 0)),
                    "TDCC_OPEN_DATA_1_5",
                ),
            )

    result = rebuild_tdcc_equity_summaries_from_persisted_distribution(conn)
    latest = conn.execute(
        "SELECT * FROM tdcc_equity_summary WHERE code='2454' ORDER BY date DESC LIMIT 1"
    ).fetchone()

    assert result["summary_count"] == 5
    assert latest["small_10_share_pct"] == 6.0
    assert latest["big_1000_change_4w"] == 4.0
