from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.official_credit_balance import (  # noqa: E402
    TPEX_LENDING_URL,
    TPEX_MARGIN_URL,
    TWSE_LENDING_URL,
    TWSE_MARGIN_URL,
    fetch_tpex_lending_balance,
    fetch_tpex_margin_balance,
    fetch_twse_lending_balance,
    fetch_twse_margin_balance,
)
from repository.credit_balance_repository import upsert_official_credit_balances  # noqa: E402
from services import official_credit_balance_service as service  # noqa: E402


def _fake_get(url: str, **kwargs):
    if url == TWSE_MARGIN_URL:
        return {
            "stat": "OK",
            "date": "20260821",
            "tables": [{
                "title": "115年08月21日 融資融券彙總 (全部)",
                "data": [["2330", "台積電", "10", "3", "1", "100", "106", "500", "4", "2", "1", "20", "17", "500", "0", ""]],
            }],
        }
    if url == TWSE_LENDING_URL:
        return {
            "stat": "OK",
            "date": "20260821",
            "data": [["2330", "台積電", "20,000", "2,000", "4,000", "1,000", "17,000", "500,000", "100,000", "8,000", "3,000", "1,000", "106,000", "30,000", ""]],
        }
    if url == TPEX_MARGIN_URL:
        assert kwargs["params"]["d"] == "115/08/21"
        return {
            "stat": "OK", "date": "20260821",
            "tables": [{"data": [[
                "6488", "環球晶", "100", "10", "3", "1", "106", "4",
                "21.2", "500", "20", "2", "4", "1", "17", "1",
                "3.4", "500", "0", "",
            ]]}],
        }
    if url == TPEX_LENDING_URL:
        assert kwargs["params"]["d"] == "115/08/21"
        return {
            "stat": "OK", "date": "20260821",
            "tables": [{"data": [[
                "6488", "環球晶", "20", "2", "4", "1", "17", "500000",
                "100000", "8000", "3000", "1000", "106000", "30000", "",
            ]]}],
        }
    raise AssertionError(url)


def test_official_credit_adapters_preserve_dates_units_and_components() -> None:
    twse_margin = fetch_twse_margin_balance("2026-08-21", http_get=_fake_get)
    twse_lending = fetch_twse_lending_balance("2026-08-21", http_get=_fake_get)
    tpex_margin = fetch_tpex_margin_balance("2026-08-21", http_get=_fake_get)
    tpex_lending = fetch_tpex_lending_balance("2026-08-21", http_get=_fake_get)

    assert {item["data_date"] for item in (twse_margin, twse_lending, tpex_margin, tpex_lending)} == {"2026-08-21"}
    assert twse_margin["items"][0]["margin_balance_lots"] == 106
    assert twse_margin["items"][0]["margin_utilization_pct"] == 21.2
    assert twse_margin["items"][0]["margin_utilization_method"] == "derived_official_balance_limit"
    assert twse_margin["items"][0]["short_balance_lots"] == 17
    assert twse_lending["items"][0]["sbl_balance_shares"] == 106000
    assert tpex_margin["items"][0]["margin_prev_balance_lots"] == 100
    assert tpex_margin["items"][0]["margin_utilization_pct"] == 21.2
    assert tpex_margin["items"][0]["margin_limit_lots"] == 500
    assert tpex_lending["items"][0]["sbl_adjust_shares"] == 1000


def test_repository_writes_detailed_and_legacy_balances_with_explicit_units() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE margin_daily(
            date TEXT,code TEXT,margin_delta REAL,margin_balance REAL,
            short_delta REAL,short_balance REAL,source TEXT,updated_at REAL,
            source_quality TEXT,fetched_at REAL,PRIMARY KEY(date,code)
        );
        CREATE TABLE lending_daily(
            date TEXT,code TEXT,lending_delta REAL,lending_balance REAL,
            source TEXT,updated_at REAL,PRIMARY KEY(date,code)
        );
        """
    )
    rows = [{
        "trade_date": "2026-08-21", "code": "2330", "market": "listed",
        "margin_prev_balance_lots": 100, "margin_buy_lots": 10,
        "margin_sell_lots": 3, "margin_cash_repayment_lots": 1,
        "margin_balance_lots": 106, "margin_utilization_pct": 21.2,
        "margin_utilization_method": "source_reported",
        "margin_limit_lots": 500, "short_prev_balance_lots": 20,
        "short_sell_lots": 2, "short_buy_lots": 4,
        "short_stock_repayment_lots": 1, "short_balance_lots": 17,
        "short_utilization_pct": 3.4, "short_limit_lots": 500,
        "short_utilization_method": "source_reported",
        "sbl_prev_balance_shares": 100000, "sbl_sell_shares": 8000,
        "sbl_return_shares": 3000, "sbl_adjust_shares": 1000,
        "sbl_balance_shares": 106000, "margin_source": "TWSE_MI_MARGN",
        "lending_source": "TWSE_TWT93U",
    }]

    assert upsert_official_credit_balances(conn, rows) == 1
    detailed = conn.execute(
        """SELECT margin_delta_lots,short_delta_lots,sbl_delta_shares,usable_from,
                  margin_utilization_pct,margin_limit_lots,short_utilization_pct,short_limit_lots
           FROM credit_balance_daily"""
    ).fetchone()
    margin = conn.execute("SELECT margin_delta,short_delta,margin_unit,short_unit FROM margin_daily").fetchone()
    lending = conn.execute("SELECT lending_delta,lending_unit FROM lending_daily").fetchone()
    versions = conn.execute(
        "SELECT dataset_key,revision_no FROM data_observation_version ORDER BY dataset_key"
    ).fetchall()
    assert detailed[:3] == (6, -3, 6000)
    assert detailed[3]
    assert detailed[4:] == (21.2, 500, 3.4, 500)
    assert margin == (6.0, -3.0, "lots", "lots")
    assert lending == (6000.0, "shares")
    assert versions == [("lending_daily", 1), ("margin_daily", 1)]

    assert upsert_official_credit_balances(conn, rows) == 1
    assert conn.execute("SELECT COUNT(*) FROM data_observation_version").fetchone()[0] == 2


def test_service_keeps_available_market_rows_when_other_market_is_delayed() -> None:
    def source(data_date: str, code: str, market: str, kind: str):
        row = {"trade_date": data_date, "code": code, "market": market, "source": kind}
        if kind.endswith("margin"):
            row.update({
                "margin_prev_balance_lots": 100,
                "margin_buy_lots": 10,
                "margin_sell_lots": 3,
                "margin_cash_repayment_lots": 1,
                "margin_balance_lots": 106,
            })
        else:
            row.update({
                "sbl_prev_balance_shares": 100_000,
                "sbl_sell_shares": 8_000,
                "sbl_return_shares": 3_000,
                "sbl_adjust_shares": 1_000,
                "sbl_balance_shares": 106_000,
            })
        return lambda _requested: {
            "ok": True,
            "source": kind,
            "data_date": data_date,
            "items": [row],
            "row_count": 1,
        }

    fake_db = MagicMock()
    with (
        patch.object(service, "db", return_value=fake_db),
        patch.object(service, "active_stock_codes", side_effect=lambda _conn, market: ["2330"] if market == "listed" else ["6488"]),
    ):
        result = service.refresh_official_credit_balances(
            "2026-08-25",
            dry_run=True,
            fetchers=(
                source("2026-08-25", "2330", "listed", "twse_margin"),
                source("2026-08-25", "2330", "listed", "twse_lending"),
                source("2026-08-24", "6488", "otc", "tpex_margin"),
                source("2026-08-24", "6488", "otc", "tpex_lending"),
            ),
        )

    assert result["ok"] is False
    assert result["status"] == "dry_run"
    assert result["row_count"] == 2
    assert result["listed_rows"] == 1
    assert result["otc_rows"] == 1
    assert result["market_results"][0]["status"] == "ok"
    assert result["market_results"][1]["status"] == "source_delayed"


def test_service_excludes_isolated_official_formula_exception_without_imputation() -> None:
    data_date = "2026-08-19"

    def fetcher(rows, source_name):
        return lambda _requested: {
            "ok": True,
            "source": source_name,
            "data_date": data_date,
            "items": rows,
            "row_count": len(rows),
        }

    listed_margin = [
        {
            "trade_date": data_date,
            "code": code,
            "market": "listed",
            "margin_prev_balance_lots": 100,
            "margin_buy_lots": 10,
            "margin_sell_lots": 3,
            "margin_cash_repayment_lots": 1,
            "margin_balance_lots": 106,
            "source": "TWSE_MI_MARGN",
        }
        for code in ("2330", "8105")
    ]
    listed_lending = [
        {
            "trade_date": data_date,
            "code": "2330",
            "market": "listed",
            "sbl_prev_balance_shares": 100,
            "sbl_sell_shares": 10,
            "sbl_return_shares": 3,
            "sbl_adjust_shares": 1,
            "sbl_balance_shares": 108,
            "source": "TWSE_TWT93U",
        },
        {
            "trade_date": data_date,
            "code": "8105",
            "market": "listed",
            "sbl_prev_balance_shares": 7_982_000,
            "sbl_sell_shares": 0,
            "sbl_return_shares": 0,
            "sbl_adjust_shares": 0,
            "sbl_balance_shares": 0,
            "source": "TWSE_TWT93U",
        },
    ]
    otc_margin = [{**listed_margin[0], "code": "6488", "market": "otc"}]
    otc_lending = [{**listed_lending[0], "code": "6488", "market": "otc"}]
    fake_db = MagicMock()
    with (
        patch.object(service, "db", return_value=fake_db),
        patch.object(
            service,
            "active_stock_codes",
            side_effect=lambda _conn, market: (
                ["2330", "8105"] if market == "listed" else ["6488"]
            ),
        ),
    ):
        result = service.refresh_official_credit_balances(
            data_date,
            dry_run=True,
            fetchers=(
                fetcher(listed_margin, "twse_margin"),
                fetcher(listed_lending, "twse_lending"),
                fetcher(otc_margin, "tpex_margin"),
                fetcher(otc_lending, "tpex_lending"),
            ),
        )

    assert result["ok"] is True
    assert result["source_row_count"] == 3
    assert result["row_count"] == 2
    assert result["excluded_formula_rows"] == 1
    assert result["excluded_formula_examples"] == [{"code": "8105", "field": "sbl"}]
