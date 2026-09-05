from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from repository.taiwan50_close_batch_repository import (  # noqa: E402
    cleanup_taiwan50_close_batch,
    ensure_taiwan50_close_batch_schema,
    latest_taiwan50_close_batch,
    upsert_taiwan50_close_batch,
)


class Taiwan50CloseBatchRepositoryTest(unittest.TestCase):
    def make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        ensure_taiwan50_close_batch_schema(conn)
        conn.execute(
            "CREATE TABLE full_market_batch_publications(trade_date TEXT PRIMARY KEY)"
        )
        return conn

    def test_upsert_latest_and_points_are_transactional(self) -> None:
        conn = self.make_conn()
        run = {
            "data_date": "2026-06-12",
            "updated_at": "2026-06-12 15:30:00",
            "item_count": 1,
            "error_count": 0,
            "source_status": "ok",
            "reason": "ok",
            "created_at": "2026-06-12 15:30:00",
        }
        upsert_taiwan50_close_batch(
            conn,
            run=run,
            items=[
                {
                    "symbol": "2330",
                    "name": "TSMC",
                    "rank_no": 1,
                    "close_price": 1000.0,
                    "reference_price": 1000.0,
                    "support_zone": {"low": 990, "high": 995},
                    "pressure_zone": None,
                    "poc_price": 990.0,
                    "poc_volume": 100,
                    "source_status": "ok",
                    "data_quality": "ok",
                    "reason": "ok",
                    "updated_at": "2026-06-12 15:30:00",
                }
            ],
            points=[
                {"symbol": "2330", "price": 990, "volume": 100, "source": "test", "updated_at": "2026-06-12 15:30:00"}
            ],
        )
        conn.execute(
            "INSERT INTO full_market_batch_publications(trade_date) VALUES(?)",
            ("2026-06-12",),
        )
        latest = latest_taiwan50_close_batch(conn)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["run"]["data_date"], "2026-06-12")
        self.assertEqual(latest["items"][0]["support_zone"]["low"], 990)

        with self.assertRaises(Exception):
            bad_run = dict(run)
            bad_run["data_date"] = "2026-06-13"
            upsert_taiwan50_close_batch(
                conn,
                run=bad_run,
                items=[],
                points=[{"symbol": "2330", "price": "bad", "volume": 1}],
            )
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM taiwan50_close_batch_runs WHERE data_date='2026-06-13'"
        ).fetchone()["c"]
        self.assertEqual(count, 0)
        conn.close()

    def test_latest_ignores_newer_unpublished_partial_batch(self) -> None:
        conn = self.make_conn()
        for data_date, price in (("2026-06-12", 1000.0), ("2026-06-13", 1015.0)):
            conn.execute(
                """
                INSERT INTO taiwan50_close_batch_runs(
                    data_date,updated_at,item_count,error_count,created_at
                ) VALUES(?,?,?,?,?)
                """,
                (data_date, f"{data_date} 15:30:00", 1, 0, f"{data_date} 15:30:00"),
            )
            conn.execute(
                """
                INSERT INTO taiwan50_close_batch_items(
                    data_date,symbol,close_price,updated_at
                ) VALUES(?,?,?,?)
                """,
                (data_date, "2330", price, f"{data_date} 15:30:00"),
            )
        conn.execute(
            "INSERT INTO full_market_batch_publications(trade_date) VALUES(?)",
            ("2026-06-12",),
        )

        latest = latest_taiwan50_close_batch(conn)
        explicit_partial = latest_taiwan50_close_batch(conn, "2026-06-13")

        self.assertEqual(latest["run"]["data_date"], "2026-06-12")
        self.assertEqual(latest["items"][0]["close_price"], 1000.0)
        self.assertEqual(explicit_partial["run"]["data_date"], "2026-06-13")
        conn.close()

    def test_cleanup_keeps_latest_200_distinct_run_dates(self) -> None:
        conn = self.make_conn()
        for i in range(201):
            d = (date(2026, 1, 1) + timedelta(days=i)).isoformat()
            conn.execute(
                """
                INSERT INTO taiwan50_close_batch_runs(data_date, updated_at, item_count, error_count, created_at)
                VALUES(?,?,?,?,?)
                """,
                (d, f"{d} 15:30:00", 0, 0, f"{d} 15:30:00"),
            )
            conn.execute(
                "INSERT INTO taiwan50_close_batch_items(data_date, symbol, updated_at) VALUES(?,?,?)",
                (d, "2330", f"{d} 15:30:00"),
            )
            conn.execute(
                "INSERT INTO taiwan50_close_volume_profile_points(data_date, symbol, price, volume, updated_at) VALUES(?,?,?,?,?)",
                (d, "2330", 100, 1, f"{d} 15:30:00"),
            )
        conn.commit()
        result = cleanup_taiwan50_close_batch(conn, retention_days=200)
        self.assertEqual(len(result["deleted_dates"]), 1)
        self.assertEqual(result["total_dates_after"], 200)
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM taiwan50_close_batch_runs").fetchone()["c"], 200)
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM taiwan50_close_batch_items").fetchone()["c"], 200)
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM taiwan50_close_volume_profile_points").fetchone()["c"], 200)
        conn.close()


if __name__ == "__main__":
    unittest.main()
