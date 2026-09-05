from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.official_trading_restrictions import (  # noqa: E402
    normalize_tpex_trading_mode,
    normalize_twse_attention,
    normalize_twse_disposition,
    parse_roc_date,
    parse_roc_period,
)
from core.trading_restriction_schema import ensure_trading_restriction_schema  # noqa: E402
from repository.trading_restriction_repository import (  # noqa: E402
    REQUIRED_SOURCES,
    read_trading_restriction_context,
    record_trading_restriction_source_runs,
    upsert_trading_restrictions,
)


TRADE_DATE = "2026-08-25"


def test_roc_date_and_period_parser_support_official_formats() -> None:
    assert parse_roc_date("115年08月25日") == TRADE_DATE
    assert parse_roc_date("1150825") == TRADE_DATE
    assert parse_roc_period("115/08/26~115/09/08") == (
        "2026-08-26",
        "2026-09-08",
    )


def test_twse_empty_notice_placeholder_is_not_treated_as_a_stock() -> None:
    assert normalize_twse_attention({"Date": "", "Code": ""}, TRADE_DATE) == []


def test_twse_disposition_uses_effective_period() -> None:
    rows = normalize_twse_disposition(
        {
            "Date": "1150825",
            "Code": "1234",
            "Name": "測試公司",
            "DispositionPeriod": "115/08/26~115/09/08",
            "Detail": "處置原因",
        },
        TRADE_DATE,
    )

    assert len(rows) == 1
    assert rows[0]["restriction_type"] == "disposition"
    assert rows[0]["effective_from"] == "2026-08-26"
    assert rows[0]["effective_to"] == "2026-09-08"


def test_tpex_trading_mode_expands_each_official_flag() -> None:
    rows = normalize_tpex_trading_mode(
        {
            "Date": "1150825",
            "SecuritiesCompanyCode": "6543",
            "CompanyName": "測試上櫃公司",
            "AlteredTrading": "Y",
            "PeriodicTrading": "Y",
            "ManagedStock": "N",
            "SuspensionOfTrading": "N",
        },
        TRADE_DATE,
    )

    assert {row["restriction_type"] for row in rows} == {
        "altered_trading",
        "periodic_trading",
    }


def test_repository_requires_successful_same_day_source_proof() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_trading_restriction_schema(conn)
    event = normalize_twse_disposition(
        {
            "Date": "1150825",
            "Code": "1234",
            "Name": "測試公司",
            "DispositionPeriod": "115/08/26~115/09/08",
            "Detail": "處置原因",
        },
        TRADE_DATE,
    )[0]
    upsert_trading_restrictions(conn, [event])
    record_trading_restriction_source_runs(
        conn,
        [
            {
                "data_date": TRADE_DATE,
                "market": "listed",
                "source_id": source_id,
                "ok": True,
                "rows_received": 0,
            }
            for source_id in REQUIRED_SOURCES["listed"]
        ],
    )

    announcement_day = read_trading_restriction_context(
        conn,
        code="1234",
        market="listed",
        reference_date=TRADE_DATE,
    )
    effective_day = read_trading_restriction_context(
        conn,
        code="1234",
        market="listed",
        reference_date="2026-08-26",
    )

    assert announcement_day["ready"] is True
    assert announcement_day["items"][0]["restriction_type"] == "disposition"
    assert effective_day["ready"] is False
    assert effective_day["items"][0]["restriction_type"] == "disposition"


def test_repository_marks_missing_source_run_as_delayed() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_trading_restriction_schema(conn)

    context = read_trading_restriction_context(
        conn,
        code="1234",
        market="listed",
        reference_date=TRADE_DATE,
    )

    assert context["ready"] is False
    assert context["status"] == "source_delayed"
    assert context["items"] == []


def test_repository_keeps_open_ended_trading_halt_active() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_trading_restriction_schema(conn)
    upsert_trading_restrictions(
        conn,
        [{
            "event_key": "twse_halt:1589:2026-08-13",
            "code": "1589",
            "company_name": "永冠-KY",
            "market": "listed",
            "restriction_type": "trading_halt",
            "announcement_date": "2026-08-11",
            "effective_from": "2026-08-13",
            "effective_to": None,
            "reason": "官方公告停止買賣，尚未公告恢復日",
            "source_id": "twse_halt",
            "source_quality": "official",
        }],
    )

    context = read_trading_restriction_context(
        conn,
        code="1589",
        market="listed",
        reference_date="2026-08-26",
    )

    assert context["items"][0]["restriction_type"] == "trading_halt"
    assert context["items"][0]["effective_to"] is None
