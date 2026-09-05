from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services import price_volume_daily_service as service  # noqa: E402


def test_requested_date_is_not_treated_as_official_when_sources_are_delayed() -> None:
    assert service.official_result_date(
        {
            "run_date": "2026-08-28",
            "official_sources": [
                {"status": "SOURCE_DELAYED", "data_date": "2026-08-27"}
            ],
        }
    ) is None


def test_exact_persisted_batch_can_supply_explicit_verified_trade_date() -> None:
    assert service.official_result_date(
        {
            "verified_trade_date": "2026-08-28",
            "official_sources": [],
        }
    ) == "2026-08-28"


def test_full_market_reconciliation_excludes_official_no_trade_rows() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE stock_no_trade_dates("
        "trade_date TEXT,code TEXT,market TEXT,reason TEXT,source TEXT,source_quality TEXT)"
    )
    conn.execute(
        "CREATE TABLE history_price(date TEXT,code TEXT,volume INTEGER,source_quality TEXT)"
    )
    conn.execute(
        "INSERT INTO stock_no_trade_dates(trade_date,code) VALUES(?,?)",
        ("2026-08-24", "2222"),
    )
    conn.execute(
        "INSERT INTO history_price(date,code,volume,source_quality) VALUES(?,?,?,?)",
        ("2026-08-24", "1111", 1000, "official"),
    )

    with (
        patch.object(service, "db", return_value=conn),
        patch.object(service, "active_stock_codes", return_value=["1111", "2222"]),
        patch.object(service, "codes_with_price_volume_distribution", return_value=["1111"]) as captured,
        patch.object(
            service,
            "reconcile_price_volume_profile_for_code",
            return_value={"ok": True, "status": "validated", "writes_db": True},
        ),
    ):
        result = service.reconcile_full_market_price_volume_after_official_update(
            {
                "run_date": "2026-08-24",
                "official_sources": [{"status": "OK", "data_date": "2026-08-24"}],
            }
        )

    captured.assert_called_once_with(["1111"], "2026-08-24")
    assert result["ok"] is True
    assert result["universe_count"] == 2
    assert result["official_no_trade_count"] == 1
    assert result["official_no_trade_codes"] == ["2222"]
    assert result["official_no_trade_details"][0]["code"] == "2222"
    assert result["official_unobserved_count"] == 0
    assert result["required_trading_stock_count"] == 1
    assert result["missing_capture_count"] == 0
    assert result["distribution_validated_count"] == 1
    assert result["scoped_validated_count"] == 0
    assert result["capture_validated_count"] == 1
    assert result["decision_ready_count"] == 0


def test_scoped_validated_profile_counts_as_complete_capture_not_decision_ready() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE stock_no_trade_dates("
        "trade_date TEXT,code TEXT,market TEXT,reason TEXT,source TEXT,source_quality TEXT)"
    )
    conn.execute(
        "CREATE TABLE history_price(date TEXT,code TEXT,volume INTEGER,source_quality TEXT)"
    )
    conn.execute(
        "INSERT INTO history_price(date,code,volume,source_quality) VALUES(?,?,?,?)",
        ("2026-08-24", "1111", 1000, "official"),
    )

    with (
        patch.object(service, "db", return_value=conn),
        patch.object(service, "active_stock_codes", return_value=["1111"]),
        patch.object(service, "codes_with_price_volume_distribution", return_value=["1111"]),
        patch.object(
            service,
            "reconcile_price_volume_profile_for_code",
            return_value={
                "ok": True,
                "status": "scoped_validated",
                "score_available": False,
                "writes_db": True,
            },
        ),
    ):
        result = service.reconcile_full_market_price_volume_after_official_update(
            {
                "run_date": "2026-08-24",
                "official_sources": [{"status": "OK", "data_date": "2026-08-24"}],
            }
        )

    assert result["ok"] is True
    assert result["validated_count"] == 0
    assert result["scoped_validated_count"] == 1
    assert result["capture_validated_count"] == 1
    assert result["decision_ready_count"] == 0


def test_full_market_report_preserves_non_ready_score_reason() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE stock_no_trade_dates("
        "trade_date TEXT,code TEXT,market TEXT,reason TEXT,source TEXT,source_quality TEXT)"
    )
    conn.execute(
        "CREATE TABLE history_price(date TEXT,code TEXT,volume INTEGER,source_quality TEXT)"
    )
    conn.execute(
        "INSERT INTO history_price(date,code,volume,source_quality) VALUES(?,?,?,?)",
        ("2026-08-24", "1111", 1000, "official"),
    )

    with (
        patch.object(service, "db", return_value=conn),
        patch.object(service, "active_stock_codes", return_value=["1111"]),
        patch.object(service, "codes_with_price_volume_distribution", return_value=["1111"]),
        patch.object(
            service,
            "reconcile_price_volume_profile_for_code",
            return_value={
                "ok": True,
                "status": "validated",
                "score_available": False,
                "score_status": "accumulating",
                "score_quality_reason": "5/30 validated Fugle days",
                "coverage_days": 5,
                "required_days": 30,
                "writes_db": True,
            },
        ),
    ):
        result = service.reconcile_full_market_price_volume_after_official_update(
            {"verified_trade_date": "2026-08-24", "official_sources": []}
        )

    item = result["results"][0]
    assert item["score_status"] == "accumulating"
    assert item["score_quality_reason"] == "5/30 validated Fugle days"
    assert item["coverage_days"] == 5
    assert item["required_days"] == 30


def test_full_market_reconciliation_does_not_treat_official_unobserved_as_missing() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE stock_no_trade_dates("
        "trade_date TEXT,code TEXT,market TEXT,reason TEXT,source TEXT,source_quality TEXT)"
    )
    conn.execute(
        "CREATE TABLE history_price(date TEXT,code TEXT,volume INTEGER,source_quality TEXT)"
    )
    conn.execute(
        "INSERT INTO history_price(date,code,volume,source_quality) VALUES(?,?,?,?)",
        ("2026-08-24", "1111", 1000, "official"),
    )

    with (
        patch.object(service, "db", return_value=conn),
        patch.object(service, "active_stock_codes", return_value=["1111", "3333"]),
        patch.object(service, "codes_with_price_volume_distribution", return_value=["1111"]),
        patch.object(
            service,
            "reconcile_price_volume_profile_for_code",
            return_value={"ok": True, "status": "validated", "writes_db": True},
        ),
    ):
        result = service.reconcile_full_market_price_volume_after_official_update(
            {
                "run_date": "2026-08-24",
                "official_sources": [{"status": "OK", "data_date": "2026-08-24"}],
            }
        )

    assert result["ok"] is True
    assert result["official_unobserved_count"] == 1
    assert result["official_unobserved_codes"] == ["3333"]
    assert result["required_trading_stock_count"] == 1


def test_missing_all_required_distributions_is_not_reported_as_success() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE stock_no_trade_dates("
        "trade_date TEXT,code TEXT,market TEXT,reason TEXT,source TEXT,source_quality TEXT)"
    )
    conn.execute(
        "CREATE TABLE history_price(date TEXT,code TEXT,volume INTEGER,source_quality TEXT)"
    )
    conn.execute(
        "INSERT INTO history_price(date,code,volume,source_quality) VALUES(?,?,?,?)",
        ("2026-08-24", "1111", 1000, "official"),
    )

    with (
        patch.object(service, "db", return_value=conn),
        patch.object(service, "active_stock_codes", return_value=["1111"]),
        patch.object(service, "codes_with_price_volume_distribution", return_value=[]),
    ):
        result = service.reconcile_full_market_price_volume_after_official_update(
            {
                "run_date": "2026-08-24",
                "official_sources": [{"status": "OK", "data_date": "2026-08-24"}],
            }
        )

    assert result["ok"] is False
    assert result["status"] == "source_delayed"
    assert result["missing_capture_count"] == 1
    assert result["operational_complete"] is False


def test_missing_distribution_is_losslessly_rebuilt_from_regular_session_trades() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE fugle_intraday_trades(
            code TEXT,trade_date TEXT,trade_time TEXT,price REAL,size INTEGER,source TEXT
        );
        CREATE TABLE fugle_intraday_capture_runs(
            code TEXT,trade_date TEXT,endpoint TEXT,source TEXT,
            normalized_row_count INTEGER,stored_row_count INTEGER,data_quality TEXT,
            snapshot_time TEXT
        );
        CREATE TABLE price_volume_distribution(
            stock_id TEXT,trade_date TEXT,volume_lots INTEGER,
            data_quality TEXT,source_quality TEXT
        );
        INSERT INTO fugle_intraday_capture_runs VALUES(
            '1111','2026-08-24','trades','FUGLE',3,3,'SESSION_COMPLETE','2026-08-24 13:40:00+08:00'
        );
        INSERT INTO fugle_intraday_trades VALUES
            ('1111','2026-08-24','09:00:00.000000',10.0,5,'FUGLE'),
            ('1111','2026-08-24','13:30:00.000000',10.0,7,'FUGLE'),
            ('1111','2026-08-24','14:30:00.000000',10.0,99,'FUGLE');
        """
    )
    with (
        patch.object(service, "db", return_value=conn),
        patch.object(
            service,
            "capture_fugle_price_volume_snapshot",
            return_value={"ok": True, "status": "captured", "price_level_count": 1, "writes_db": True},
        ) as capture,
    ):
        result = service.recover_missing_price_volume_from_persisted_trades("2026-08-24")

    assert result["recovered_count"] == 1
    payload = capture.call_args.args[1]
    assert payload["date"] == "2026-08-24"
    assert payload["data"] == [{"price": 10.0, "volume": 12}]
    assert capture.call_args.kwargs["verified_snapshot_time"] == "2026-08-24 13:40:00+08:00"


def test_incompatible_unvalidated_distribution_is_rebuilt_from_exact_trades() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE fugle_intraday_trades(
            code TEXT,trade_date TEXT,trade_time TEXT,price REAL,size INTEGER,source TEXT
        );
        CREATE TABLE fugle_intraday_capture_runs(
            code TEXT,trade_date TEXT,endpoint TEXT,source TEXT,
            normalized_row_count INTEGER,stored_row_count INTEGER,data_quality TEXT,
            snapshot_time TEXT
        );
        CREATE TABLE price_volume_distribution(
            stock_id TEXT,trade_date TEXT,volume_lots INTEGER,
            data_quality TEXT,source_quality TEXT
        );
        INSERT INTO fugle_intraday_capture_runs VALUES(
            '1111','2026-08-24','trades','FUGLE',2,2,'SESSION_COMPLETE','2026-08-24 13:40:00+08:00'
        );
        INSERT INTO fugle_intraday_trades VALUES
            ('1111','2026-08-24','09:00:00.000000',10.0,2,'FUGLE'),
            ('1111','2026-08-24','13:30:00.000000',11.0,2,'FUGLE');
        INSERT INTO price_volume_distribution VALUES(
            '1111','2026-08-24',3,'VOLUME_MISMATCH','VOLUME_MISMATCH'
        );
        """
    )
    with (
        patch.object(service, "db", return_value=conn),
        patch.object(
            service,
            "capture_fugle_price_volume_snapshot",
            return_value={"ok": True, "status": "captured", "price_level_count": 2, "writes_db": True},
        ) as capture,
    ):
        result = service.recover_missing_price_volume_from_persisted_trades(
            "2026-08-24",
            verified_official_trade_date="2026-08-24",
        )

    assert result["recovered_count"] == 1
    assert result["replaced_incompatible_count"] == 1
    assert result["results"][0]["recovery_reason"] == "trade_distribution_mismatch"
    assert capture.call_args.kwargs["allow_delayed_terminal_pagination"] is True
    assert capture.call_args.args[1]["data"] == [
        {"price": 10.0, "volume": 2},
        {"price": 11.0, "volume": 2},
    ]


def test_validated_distribution_is_never_replaced() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE fugle_intraday_trades(
            code TEXT,trade_date TEXT,trade_time TEXT,price REAL,size INTEGER,source TEXT
        );
        CREATE TABLE fugle_intraday_capture_runs(
            code TEXT,trade_date TEXT,endpoint TEXT,source TEXT,
            normalized_row_count INTEGER,stored_row_count INTEGER,data_quality TEXT,
            snapshot_time TEXT
        );
        CREATE TABLE price_volume_distribution(
            stock_id TEXT,trade_date TEXT,volume_lots INTEGER,
            data_quality TEXT,source_quality TEXT
        );
        INSERT INTO fugle_intraday_capture_runs VALUES(
            '1111','2026-08-24','trades','FUGLE',1,1,'SESSION_COMPLETE','2026-08-24 13:40:00+08:00'
        );
        INSERT INTO fugle_intraday_trades VALUES(
            '1111','2026-08-24','13:30:00.000000',10.0,10,'FUGLE'
        );
        INSERT INTO price_volume_distribution VALUES(
            '1111','2026-08-24',1,'VALIDATED','VALIDATED'
        );
        """
    )
    with (
        patch.object(service, "db", return_value=conn),
        patch.object(service, "capture_fugle_price_volume_snapshot") as capture,
    ):
        result = service.recover_missing_price_volume_from_persisted_trades("2026-08-24")

    assert result["recovered_count"] == 0
    assert result["status"] == "skipped_no_candidates"
    capture.assert_not_called()
