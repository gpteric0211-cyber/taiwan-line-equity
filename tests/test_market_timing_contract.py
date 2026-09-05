from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.market_timing import availability_contract  # noqa: E402
from repository import external_event_repository, news_radar_repository  # noqa: E402
from repository.external_event_repository import (  # noqa: E402
    read_stock_external_market_events,
    upsert_external_market_events,
)
from repository.global_market_repository import (  # noqa: E402
    read_latest_global_market_rows,
    upsert_global_market_rows,
)
from repository.news_radar_repository import (  # noqa: E402
    read_stock_news_radar_events,
    upsert_news_radar_events,
)


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_availability_contract_maps_taiwan_sessions_to_effective_trade_date() -> None:
    pre_market = availability_contract(fetched_at="2026-08-28T06:15:00+08:00")
    post_market = availability_contract(fetched_at="2026-08-28T18:00:00+08:00")

    assert pre_market["market_session"] == "pre_market"
    assert pre_market["effective_tw_trade_date"] == "2026-08-28"
    assert post_market["market_session"] == "post_market"
    assert post_market["effective_tw_trade_date"] == "2026-08-31"


def test_global_market_reader_enforces_effective_taiwan_trade_date() -> None:
    conn = _connection()
    rows = [
        {
            "market_date": "2026-08-27",
            "ticker": "TSM",
            "display_name": "TSMC ADR",
            "close": 100.0,
            "previous_close": 99.0,
            "change_pct": 1.01,
            "currency": "USD",
            "source": "licensed test",
            "source_quality": "supplemental",
            "fetched_at": "2026-08-28T06:15:00+08:00",
            "available_at": "2026-08-28T06:15:00+08:00",
            "market_session": "pre_market",
            "effective_tw_trade_date": "2026-08-28",
            "exchange_timezone": "America/New_York",
            "source_market_timestamp": "2026-08-27T16:00:00-04:00",
        },
        {
            "market_date": "2026-08-27",
            "ticker": "^SOX",
            "display_name": "SOX",
            "close": 200.0,
            "previous_close": 198.0,
            "change_pct": 1.01,
            "currency": "USD",
            "source": "licensed test",
            "source_quality": "supplemental",
            "fetched_at": "2026-08-28T18:00:00+08:00",
            "available_at": "2026-08-28T18:00:00+08:00",
            "market_session": "post_market",
            "effective_tw_trade_date": "2026-08-31",
            "exchange_timezone": "America/New_York",
            "source_market_timestamp": "2026-08-27T16:00:00-04:00",
        },
    ]
    upsert_global_market_rows(conn, rows)

    found = read_latest_global_market_rows(conn, reference_date="2026-08-27")

    assert [row["ticker"] for row in found] == ["TSM"]
    assert found[0]["effective_tw_trade_date"] == "2026-08-28"


def test_news_repository_persists_point_in_time_fields(monkeypatch) -> None:
    class FakeDateTime:
        @staticmethod
        def now():
            return datetime(2026, 8, 27, 8, 0, tzinfo=ZoneInfo("Asia/Taipei"))

    monkeypatch.setattr(news_radar_repository, "datetime", FakeDateTime)
    conn = _connection()
    upsert_news_radar_events(
        conn,
        [
            {
                "event_key": "n1",
                "event_date": "2026-08-27",
                "published_at": "2026-08-27T07:30:00+08:00",
                "source_id": "TEST",
                "publisher": "test",
                "source_url": "https://example.com/n1",
                "title": "鴻海伺服器消息",
                "affected_terms": ["鴻海"],
                "quality_status": "ok",
                "license_class": "metadata_only",
                "analysis_version": "test-v1",
                "reliability_score": 0.5,
                "reference_value_score": 0.5,
            }
        ],
    )

    found = read_stock_news_radar_events(
        conn,
        reference_date="2026-08-26",
        stock_terms=["鴻海"],
    )

    assert len(found) == 1
    assert found[0]["available_at"] == "2026-08-27T08:00:00+08:00"
    assert found[0]["market_session"] == "pre_market"
    assert found[0]["effective_tw_trade_date"] == "2026-08-27"


def test_external_event_repository_persists_effective_date(monkeypatch) -> None:
    class FakeDateTime:
        @staticmethod
        def now():
            return datetime(2026, 8, 27, 18, 0, tzinfo=ZoneInfo("Asia/Taipei"))

    monkeypatch.setattr(external_event_repository, "datetime", FakeDateTime)
    conn = _connection()
    upsert_external_market_events(
        conn,
        [
            {
                "event_key": "e1",
                "event_date": "2026-08-27",
                "published_at": "2026-08-27T17:30:00+08:00",
                "code": "2330",
                "source_id": "MOPS",
                "publisher": "官方來源",
                "source_url": "https://example.com/e1",
                "source_class": "official_filing",
                "event_type": "material_event",
                "title": "重大訊息",
                "direction": "unknown",
                "confidence": "low",
                "time_horizon": "short",
                "quality_status": "ok",
                "source_quality": "official",
                "license_class": "official_open_data",
                "analysis_version": "test-v1",
                "reliability_score": 1.0,
                "reference_value_score": 0.8,
            }
        ],
    )

    found = read_stock_external_market_events(
        conn,
        code="2330",
        reference_date="2026-08-27",
        stock_terms=["半導體"],
    )

    assert len(found) == 1
    assert found[0]["market_session"] == "post_market"
    assert found[0]["effective_tw_trade_date"] == "2026-08-28"
