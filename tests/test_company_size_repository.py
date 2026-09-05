from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.tpex import normalize_tpex_stock_row  # noqa: E402
from adapter.twse_company import normalize_twse_company_row  # noqa: E402
from core.company_size_schema import ensure_company_size_schema  # noqa: E402
from repository.company_size_repository import (  # noqa: E402
    read_company_size_context,
    read_company_size_rows_at_cutoff,
    upsert_company_size_snapshots,
)


def test_official_company_rows_keep_point_in_time_size_values() -> None:
    listed = normalize_twse_company_row(
        {
            "出表日期": "1150825",
            "公司代號": "2330",
            "公司簡稱": "台積電",
            "實收資本額": "259303804580",
            "已發行普通股數或TDR原股發行股數": "25930380458",
        }
    )
    otc = normalize_tpex_stock_row(
        {
            "Date": "1150825",
            "SecuritiesCompanyCode": "3491",
            "CompanyName": "昇達科",
            "Paidin.Capital.NTDollars": "1000000000",
            "IssueShares": "100000000",
        }
    )

    assert listed["data_date"] == "2026-08-25"
    assert listed["paid_in_capital_twd"] == 259_303_804_580
    assert listed["issued_shares"] == 25_930_380_458
    assert otc["data_date"] == "2026-08-25"
    assert otc["paid_in_capital_twd"] == 1_000_000_000


def test_company_size_snapshot_is_point_in_time_and_freshness_gated() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_company_size_schema(conn)
    upsert_company_size_snapshots(
        conn,
        [
            {
                "data_date": "2026-08-25",
                "code": "2330",
                "market": "listed",
                "paid_in_capital_twd": 259_303_804_580,
                "issued_shares": 25_930_380_458,
                "source_id": "TWSE_COMPANY_OPENAPI",
            }
        ],
    )

    same_day = read_company_size_context(
        conn,
        code="2330",
        reference_date="2026-08-25",
    )
    before_snapshot = read_company_size_context(
        conn,
        code="2330",
        reference_date="2026-08-24",
    )
    stale = read_company_size_context(
        conn,
        code="2330",
        reference_date="2026-10-20",
    )

    assert same_day["ready"] is True
    assert before_snapshot["ready"] is False
    assert stale["ready"] is False
    assert stale["status"] == "stale"


def test_company_size_cutoff_reader_is_offset_aware_read_only_and_point_in_time() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_company_size_schema(conn)
    upsert_company_size_snapshots(
        conn,
        [
            {
                "data_date": "2026-08-24",
                "code": "2454",
                "market": "listed",
                "paid_in_capital_twd": 15_000_000_000,
                "issued_shares": 1_500_000_000,
                "source_id": "TWSE_COMPANY_OPENAPI",
            }
        ],
        observed_at="2026-08-25T18:00:00+08:00",
    )
    upsert_company_size_snapshots(
        conn,
        [
            {
                "data_date": "2026-09-01",
                "code": "2454",
                "market": "listed",
                "paid_in_capital_twd": 15_100_000_000,
                "issued_shares": 1_510_000_000,
                "source_id": "TWSE_COMPANY_OPENAPI",
            }
        ],
        observed_at="2026-09-02T06:35:00+08:00",
    )
    conn.execute(
        """
        INSERT INTO official_company_size_snapshot VALUES(
            '2026-08-31','2454','listed',15050000000,1505000000,
            'TWSE_COMPANY_OPENAPI','official','2026-09-02 06:00:00'
        )
        """
    )
    before = conn.total_changes

    rows = read_company_size_rows_at_cutoff(
        conn,
        code="2454",
        analysis_cutoff="2026-09-02T07:00:00+08:00",
        as_of_date="2026-09-01",
    )

    assert [row["data_date"] for row in rows] == ["2026-09-01", "2026-08-24"]
    assert rows[0]["issued_shares"] == 1_510_000_000
    assert conn.total_changes == before


def test_company_size_cutoff_reader_excludes_rows_first_seen_after_cutoff() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_company_size_schema(conn)
    upsert_company_size_snapshots(
        conn,
        [
            {
                "data_date": "2026-09-01",
                "code": "2454",
                "market": "listed",
                "paid_in_capital_twd": 15_100_000_000,
                "issued_shares": 1_510_000_000,
                "source_id": "TWSE_COMPANY_OPENAPI",
            }
        ],
        observed_at="2026-09-02T07:00:01+08:00",
    )

    assert read_company_size_rows_at_cutoff(
        conn,
        code="2454",
        analysis_cutoff="2026-09-02T07:00:00+08:00",
        as_of_date="2026-09-01",
    ) == []
