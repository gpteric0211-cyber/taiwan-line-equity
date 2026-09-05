from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services import official_event_service
from scripts import update_external_analysis_context
from core.official_event_schema import ensure_official_event_schema


def _event(market: str, disclosed_date: str) -> dict[str, str]:
    return {
        "market": market,
        "disclosed_date": disclosed_date,
        "event_key": f"{market}-{disclosed_date}",
    }


def _fetched(*rows: dict[str, str]) -> dict:
    return {
        "ok": True,
        "status": "ok",
        "items": list(rows),
        "sources": [
            {"market": "listed", "ok": True},
            {"market": "otc", "ok": True},
        ],
    }


def test_daily_event_transport_success_does_not_hide_stale_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        official_event_service,
        "fetch_official_company_events",
        lambda: _fetched(
            _event("listed", "2026-08-27"),
            _event("otc", "2026-08-27"),
        ),
    )

    result = official_event_service.refresh_official_company_events(
        dry_run=True,
        expected_date="2026-08-28",
    )

    assert result["ok"] is False
    assert result["status"] == "source_delayed"
    assert result["latest_dates"] == {
        "listed": "2026-08-27",
        "otc": "2026-08-27",
    }
    assert result["source_delayed_markets"] == ["listed", "otc"]


def test_daily_event_snapshot_is_fresh_only_when_both_markets_reach_expected_date(monkeypatch) -> None:
    monkeypatch.setattr(
        official_event_service,
        "fetch_official_company_events",
        lambda: _fetched(
            _event("listed", "2026-08-28"),
            _event("otc", "2026-08-28"),
        ),
    )

    result = official_event_service.refresh_official_company_events(
        dry_run=True,
        expected_date="2026-08-28",
    )

    assert result["ok"] is True
    assert result["status"] == "dry_run"
    assert result["source_delayed_markets"] == []


def test_external_context_forwards_the_frozen_expected_date(monkeypatch) -> None:
    calls: list[dict] = []
    ok = {"ok": True, "status": "ok"}
    monkeypatch.setattr(
        update_external_analysis_context,
        "refresh_global_market_snapshot",
        lambda **_kwargs: dict(ok),
    )
    monkeypatch.setattr(
        update_external_analysis_context,
        "refresh_taifex_night_snapshot",
        lambda **_kwargs: dict(ok),
    )
    monkeypatch.setattr(
        update_external_analysis_context,
        "refresh_official_company_events",
        lambda **kwargs: calls.append(kwargs) or dict(ok),
    )
    monkeypatch.setattr(
        update_external_analysis_context,
        "refresh_official_trading_restrictions",
        lambda **_kwargs: dict(ok),
    )
    monkeypatch.setattr(
        update_external_analysis_context,
        "refresh_external_market_events",
        lambda **_kwargs: dict(ok),
    )

    result = update_external_analysis_context.update_external_analysis_context(
        dry_run=True,
        expected_date="2026-08-28",
    )

    assert result["ok"] is True
    assert result["expected_date"] == "2026-08-28"
    assert calls == [{"dry_run": True, "expected_date": "2026-08-28"}]


def test_existing_event_rows_gain_padded_time_and_point_in_time_metadata() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE official_company_event(
            event_key TEXT PRIMARY KEY,disclosed_date TEXT NOT NULL,
            disclosed_time TEXT,fact_date TEXT,code TEXT NOT NULL,
            company_name TEXT,subject TEXT NOT NULL,explanation TEXT,
            article_code TEXT,market TEXT NOT NULL,attention_level TEXT,
            source TEXT NOT NULL,source_quality TEXT,fetched_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO official_company_event VALUES(
            'event-1','2026-08-28','70003',NULL,'2330','台積電','測試',NULL,
            NULL,'listed','normal','TWSE_MOPS_DAILY_EVENT','official',
            '2026-08-28T18:00:00+08:00'
        )
        """
    )

    ensure_official_event_schema(conn)

    row = conn.execute(
        """
        SELECT disclosed_time,available_at,market_session,effective_tw_trade_date
        FROM official_company_event WHERE event_key='event-1'
        """
    ).fetchone()
    assert row == (
        "070003",
        "2026-08-28T18:00:00+08:00",
        "post_market",
        "2026-08-31",
    )
    conn.close()
