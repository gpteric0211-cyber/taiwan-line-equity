from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.global_market_schema import ensure_global_market_schema  # noqa: E402
from core.company_size_schema import ensure_company_size_schema  # noqa: E402
from core.single_track_v3_schema import ensure_single_track_v3_schema  # noqa: E402
from core.taifex_night_schema import ensure_taifex_night_schema  # noqa: E402
from repository.global_market_repository import upsert_global_market_rows  # noqa: E402
from repository.single_track_v3_scheduler_repository import (  # noqa: E402
    create_news_retrieval_run,
)
from repository.single_track_v3_source_snapshot_repository import (  # noqa: E402
    source_snapshot_receipts_for_run,
)
from repository.twse_valuation_repository import (  # noqa: E402
    upsert_twse_daily_valuations,
)
from services.global_market_snapshot_service import GLOBAL_MARKET_TICKERS  # noqa: E402
from task.single_track_v3_source_snapshot_materializer import (  # noqa: E402
    materialize_source_snapshots_for_run,
)


TPE = ZoneInfo("Asia/Taipei")


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_single_track_v3_schema(conn)
    ensure_global_market_schema(conn)
    ensure_taifex_night_schema(conn)
    ensure_company_size_schema(conn)
    conn.execute(
        """
        INSERT INTO single_track_v3_calendar_revision(
            calendar_revision,source_id,source_url,source_digest,
            session_policy_version,revision_published_at,revision_available_at,
            timezone,revision_digest,sealed_at,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "calendar-r1",
            "TWSE_EXACT_SESSION",
            "https://www.twse.com.tw/",
            "a" * 64,
            "OfficialTWSEExactSessionSourceV1",
            "2026-01-01T09:00:00+08:00",
            "2026-01-01T09:01:00+08:00",
            "Asia/Taipei",
            "b" * 64,
            "2026-01-01T09:02:00+08:00",
            "2026-01-01T09:02:00+08:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO single_track_v3_calendar_session(
            session_id,calendar_revision,trade_date,session_state,
            scheduled_open_at,scheduled_close_at,cancellation_reason,
            source_evidence_digest,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            "session-20260901",
            "calendar-r1",
            "2026-09-01",
            "scheduled",
            "2026-09-01T09:00:00+08:00",
            "2026-09-01T13:30:00+08:00",
            None,
            "c" * 64,
            "2026-01-01T09:02:00+08:00",
        ),
    )
    create_news_retrieval_run(
        conn,
        {
            "run_id": "run-source-materializer",
            "idempotency_key": "source-materializer-20260902-final",
            "slot_key": "preopen_final_scan",
            "target_trade_date": "2026-09-02",
            "scheduled_for": "2026-09-02T06:45:00+08:00",
            "cutoff_at": "2026-09-02T07:00:00+08:00",
            "started_at": "2026-09-02T06:45:00+08:00",
            "completed_at": None,
            "status": "running",
            "source_policy_version": "SourceAuthorityPolicyV1",
            "calendar_revision": "calendar-r1",
            "source_coverage": {},
            "source_failures": [],
            "late_reason": None,
            "created_at": "2026-09-02T06:45:00+08:00",
            "updated_at": "2026-09-02T06:45:00+08:00",
        },
    )
    return conn


def _global_rows(available_at: str) -> list[dict[str, object]]:
    return [
        {
            "market_date": "2026-09-01",
            "ticker": ticker,
            "display_name": display_name,
            "close": 100.0 + index,
            "previous_close": 99.0 + index,
            "change_pct": 0.4,
            "currency": "USD",
            "source": "Yahoo Finance chart",
            "source_quality": "supplemental",
            "fetched_at": available_at,
            "available_at": available_at,
            "market_session": "preopen",
            "effective_tw_trade_date": "2026-09-02",
            "exchange_timezone": "America/New_York",
            "source_market_timestamp": "2026-09-01T16:00:00-04:00",
        }
        for index, (ticker, display_name) in enumerate(GLOBAL_MARKET_TICKERS.items())
    ]


def _insert_taifex(conn: sqlite3.Connection, fetched_at: str) -> None:
    conn.execute(
        """
        INSERT INTO taifex_night_daily_snapshot(
            trade_date,contract,contract_month,last,change_pct,volume,
            trading_session,source,source_quality,fetched_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "2026-09-01",
            "TX",
            "202609",
            25000.0,
            0.6,
            10000.0,
            "盤後",
            "TAIFEX_DAILY_MARKET_REPORT_FUT",
            "official",
            fetched_at,
        ),
    )


def _insert_valuation(conn: sqlite3.Connection, available_at: str) -> None:
    upsert_twse_daily_valuations(
        [
            {
                "data_date": "2026-09-01",
                "symbol": "2454",
                "name": "聯發科",
                "close_price": 1500.0,
                "dividend_yield": 2.0,
                "pe_ratio": 20.0,
                "pb_ratio": 4.0,
                "source": "TWSE_BWIBBU",
                "source_status": "ok",
                "updated_at": "2026-09-02 06:36:00",
                "available_at": available_at,
                "timezone": "Asia/Taipei",
            }
        ],
        conn=conn,
    )


def _insert_company_size(conn: sqlite3.Connection, fetched_at: str) -> None:
    conn.executemany(
        """
        INSERT INTO official_company_size_snapshot(
            data_date,code,market,paid_in_capital_twd,issued_shares,
            source_id,source_quality,fetched_at
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        [
            (
                "2026-08-25",
                "2454",
                "listed",
                15_000_000_000,
                1_500_000_000,
                "TWSE_COMPANY_OPENAPI",
                "official",
                "2026-08-26T18:00:00+08:00",
            ),
            (
                "2026-09-01",
                "2454",
                "listed",
                15_100_000_000,
                1_510_000_000,
                "TWSE_COMPANY_OPENAPI",
                "official",
                fetched_at,
            ),
        ],
    )


def test_materializes_four_cutoff_safe_sources_and_replays_without_rereading() -> None:
    conn = _connection()
    upsert_global_market_rows(conn, _global_rows("2026-09-02T06:30:00+08:00"))
    _insert_taifex(conn, "2026-09-02T06:35:00+08:00")
    _insert_valuation(conn, "2026-09-02T06:36:00+08:00")
    _insert_company_size(conn, "2026-09-02T06:34:00+08:00")
    entity = {"stock_code": "2454", "related_symbols": ["TSM", "^SOX"]}

    result = materialize_source_snapshots_for_run(
        conn,
        run_id="run-source-materializer",
        entity=entity,
        clock=lambda: datetime(2026, 9, 2, 7, 0, 1, tzinfo=TPE),
    )

    assert result["complete"] is True
    assert result["replayed"] is False
    assert result["source_statuses"] == {
        "dilution_valuation_snapshot": "ok",
        "related_overseas_price_snapshot": "ok",
        "taifex_night_snapshot": "ok",
        "us_market_snapshot": "ok",
    }
    assert result["network_fetches"] == result["model_calls"] == 0
    receipts = source_snapshot_receipts_for_run(conn, "run-source-materializer")
    assert len(receipts) == 4
    assert all(item["available_at"] <= "2026-09-02T07:00:00+08:00" for item in receipts)
    dilution = next(
        item for item in receipts if item["source_key"] == "dilution_valuation_snapshot"
    )
    assert dilution["authority_tier"] == "canonical_official"
    assert dilution["payload"]["component_statuses"] == {
        "dilution": "ok",
        "valuation": "ok",
    }
    comparison = next(
        row
        for row in dilution["payload"]["rows"]
        if row["record_type"] == "official_share_count_comparison"
    )
    assert comparison["issued_shares_delta"] == 10_000_000
    assert comparison["dilution_observed"] is True
    assert comparison["candidate_contribution"] == 0
    assert comparison["referee_eligible"] is False
    replay = materialize_source_snapshots_for_run(
        conn,
        run_id="run-source-materializer",
        entity=entity,
        clock=lambda: datetime(2026, 9, 2, 8, 0, 0, tzinfo=TPE),
    )
    assert replay["replayed"] is True
    assert replay["source_snapshot_ids"] == result["source_snapshot_ids"]


def test_naive_or_missing_legacy_availability_is_not_fabricated() -> None:
    conn = _connection()
    upsert_global_market_rows(conn, _global_rows("2026-09-02 06:30:00"))
    _insert_taifex(conn, "2026-09-02 06:35:00")
    _insert_valuation(conn, "2026-09-02 06:36:00")

    result = materialize_source_snapshots_for_run(
        conn,
        run_id="run-source-materializer",
        entity={"stock_code": "2454", "related_symbols": ["TSM"]},
        clock=lambda: datetime(2026, 9, 2, 7, 0, 1, tzinfo=TPE),
    )

    assert result["complete"] is False
    assert set(result["source_statuses"].values()) == {"unavailable"}
    receipts = source_snapshot_receipts_for_run(conn, "run-source-materializer")
    assert all(item["available_at"] is None for item in receipts)
    assert all(item["payload"] == {"rows": []} for item in receipts)
    assert all("offset_aware" in str(item["availability_reason"]) for item in receipts)


def test_valuation_is_retained_as_partial_when_dilution_history_is_unavailable() -> None:
    conn = _connection()
    upsert_global_market_rows(conn, _global_rows("2026-09-02T06:30:00+08:00"))
    _insert_taifex(conn, "2026-09-02T06:35:00+08:00")
    _insert_valuation(conn, "2026-09-02T06:36:00+08:00")

    result = materialize_source_snapshots_for_run(
        conn,
        run_id="run-source-materializer",
        entity={"stock_code": "2454", "related_symbols": ["TSM", "^SOX"]},
        clock=lambda: datetime(2026, 9, 2, 7, 0, 1, tzinfo=TPE),
    )

    assert result["source_statuses"]["dilution_valuation_snapshot"] == "partial"
    receipt = next(
        item
        for item in source_snapshot_receipts_for_run(conn, "run-source-materializer")
        if item["source_key"] == "dilution_valuation_snapshot"
    )
    assert receipt["payload"]["component_statuses"] == {
        "dilution": "unavailable",
        "valuation": "ok",
    }
    assert [row["record_type"] for row in receipt["payload"]["rows"]] == [
        "official_valuation"
    ]
    assert receipt["provenance"]["candidate_contribution"] == 0
