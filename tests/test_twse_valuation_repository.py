from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from repository.twse_valuation_repository import (  # noqa: E402
    cleanup_twse_daily_valuation,
    ensure_twse_daily_valuation_schema,
    get_latest_twse_valuation_date,
    get_twse_valuation,
    upsert_twse_daily_valuations,
)


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_twse_daily_valuation_schema(conn)
    return conn


class TwseValuationRepositoryTest(unittest.TestCase):
    def test_upsert_and_get_latest(self) -> None:
        conn = _memory_conn()
        try:
            upsert_twse_daily_valuations(
                [
                    {
                        "data_date": "2026-06-11",
                        "symbol": "2382",
                        "name": "廣達",
                        "close_price": 370.0,
                        "dividend_yield": 4.2,
                        "dividend_year": "113",
                        "pe_ratio": 18.6,
                        "pb_ratio": 6.8,
                        "financial_year_quarter": "114/1",
                        "source": "TWSE_BWIBBU",
                        "source_status": "ok",
                        "updated_at": "2026-06-11 15:00:00",
                        "timezone": "Asia/Taipei",
                    }
                ],
                conn,
            )
            upsert_twse_daily_valuations(
                [
                    {
                        "data_date": "2026-06-12",
                        "symbol": "2382",
                        "name": "廣達",
                        "close_price": 372.0,
                        "dividend_yield": 4.19,
                        "dividend_year": "113",
                        "pe_ratio": 18.7,
                        "pb_ratio": 6.91,
                        "financial_year_quarter": "114/1",
                        "source": "TWSE_BWIBBU",
                        "source_status": "ok",
                        "updated_at": "2026-06-12 15:00:00",
                        "timezone": "Asia/Taipei",
                    }
                ],
                conn,
            )
            latest = get_twse_valuation("2382", conn=conn)
            self.assertIsNotNone(latest)
            self.assertEqual(latest["data_date"], "2026-06-12")
            self.assertEqual(latest["pe_ratio"], 18.7)
            by_date = get_twse_valuation("2382", "2026-06-11", conn=conn)
            self.assertIsNotNone(by_date)
            self.assertEqual(by_date["pb_ratio"], 6.8)
            self.assertEqual(get_latest_twse_valuation_date(conn), "2026-06-12")
        finally:
            conn.close()

    def test_cleanup_keeps_latest_distinct_dates(self) -> None:
        conn = _memory_conn()
        try:
            rows = []
            for day in range(1, 6):
                rows.append(
                    {
                        "data_date": f"2026-06-{day:02d}",
                        "symbol": "2330",
                        "updated_at": "2026-06-01 15:00:00",
                        "source": "TWSE_BWIBBU",
                        "source_status": "ok",
                        "timezone": "Asia/Taipei",
                    }
                )
            upsert_twse_daily_valuations(rows, conn)
            result = cleanup_twse_daily_valuation(retention_days=3, conn=conn)
            self.assertEqual(result["deleted_dates"], ["2026-06-02", "2026-06-01"])
            remaining = [
                row["data_date"]
                for row in conn.execute(
                    "SELECT DISTINCT data_date FROM twse_daily_valuation ORDER BY data_date"
                ).fetchall()
            ]
            self.assertEqual(remaining, ["2026-06-03", "2026-06-04", "2026-06-05"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
