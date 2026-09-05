from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from repository.estimated_chip_cost_repository import load_cost_input_rows  # noqa: E402
from services import daily_chip_momentum_service  # noqa: E402


class PublicationMarkerCostInputTest(unittest.TestCase):
    def test_default_cost_inputs_exclude_newer_partial_day(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE full_market_batch_publications(trade_date TEXT PRIMARY KEY);
                CREATE TABLE history_price(
                    date TEXT,code TEXT,close REAL,volume REAL,amount REAL,
                    PRIMARY KEY(date,code)
                );
                CREATE TABLE institution_daily(
                    date TEXT,code TEXT,foreign_net REAL,trust_net REAL
                );
                CREATE TABLE institution_activity_daily(
                    trade_date TEXT,code TEXT,foreign_net REAL,trust_net REAL,
                    source TEXT,source_quality TEXT
                );
                CREATE TABLE margin_daily(
                    date TEXT,code TEXT,margin_balance REAL
                );
                CREATE TABLE foreign_shareholding(
                    date TEXT,code TEXT,ForeignInvestmentShares REAL
                );
                CREATE TABLE price_volume_score_daily(
                    date TEXT,code TEXT,weighted_cost REAL,main_peak_price REAL,
                    quality TEXT,status TEXT,coverage_days INTEGER,required_days INTEGER
                );
                """
            )
            conn.execute(
                "INSERT INTO full_market_batch_publications(trade_date) VALUES(?)",
                ("2026-08-26",),
            )
            conn.executemany(
                "INSERT INTO history_price VALUES(?,?,?,?,?)",
                [
                    ("2026-08-26", "1101", 24.3, 1_000_000, 24_300_000),
                    ("2026-08-27", "1101", 24.1, 200_000, 4_820_000),
                ],
            )

            default_rows = load_cost_input_rows(conn, ["1101"], 20)
            explicit_rows = load_cost_input_rows(
                conn,
                ["1101"],
                20,
                as_of_date="2026-08-27",
            )

        self.assertEqual([row.trade_date for row in default_rows], ["2026-08-26"])
        self.assertEqual(
            [row.trade_date for row in explicit_rows],
            ["2026-08-26", "2026-08-27"],
        )

    def test_daily_chip_snapshot_uses_publication_marker_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "chip.sqlite3"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.executescript(
                    """
                    CREATE TABLE full_market_batch_publications(
                        trade_date TEXT PRIMARY KEY
                    );
                    CREATE TABLE history_price(
                        date TEXT,code TEXT,close REAL,volume REAL,
                        PRIMARY KEY(date,code)
                    );
                    CREATE TABLE institution_daily(
                        date TEXT,code TEXT,foreign_net REAL,trust_net REAL,dealer_net REAL
                    );
                    CREATE TABLE margin_daily(
                        date TEXT,code TEXT,margin_delta REAL,margin_balance REAL,
                        short_delta REAL,short_balance REAL
                    );
                    """
                )
                conn.execute(
                    "INSERT INTO full_market_batch_publications(trade_date) VALUES(?)",
                    ("2026-08-26",),
                )
                conn.executemany(
                    "INSERT INTO history_price VALUES(?,?,?,?)",
                    [
                        ("2026-08-25", "1101", 24.6, 900_000),
                        ("2026-08-26", "1101", 24.3, 1_000_000),
                        ("2026-08-27", "1101", 24.1, 200_000),
                    ],
                )
                conn.commit()

            def connect() -> sqlite3.Connection:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                return conn

            with (
                patch.object(daily_chip_momentum_service, "db", side_effect=connect),
                patch.object(
                    daily_chip_momentum_service,
                    "calc_tdcc_trend",
                    return_value={"trend": "資料不足"},
                ),
            ):
                row = daily_chip_momentum_service._row_for_code(
                    "1101",
                    date=None,
                    mode="tw50",
                )

        self.assertEqual(row["date"], "2026-08-26")
        self.assertEqual(row["close"], 24.3)


if __name__ == "__main__":
    unittest.main()
