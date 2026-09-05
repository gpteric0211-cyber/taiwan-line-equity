from __future__ import annotations

import sqlite3
import sys
import inspect
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

import app  # noqa: E402
from repository import (  # noqa: E402
    external_event_repository,
    market_microstructure_repository,
    news_radar_repository,
)
from repository.external_event_repository import (  # noqa: E402
    read_stock_external_market_events,
    upsert_external_market_events,
)
from repository.news_radar_repository import (  # noqa: E402
    read_stock_news_radar_events,
    upsert_news_radar_events,
)
from services import bot_market_data_service as canonical_service  # noqa: E402


CONTRACT_VERSION = "canonical-close-batch-analysis-v1"


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


@pytest.mark.parametrize(
    ("referee", "expected_status", "expected_complete"),
    [
        (
            {
                "decision_ready": True,
                "main_status": "可觀察",
                "main_reasons": ["價格與量能條件通過"],
                "reason_code": None,
                "source": "shared_project_referee",
                "version": "referee-v1",
            },
            "ready",
            True,
        ),
        (
            {
                "decision_ready": False,
                "main_status": "不判斷",
                "main_reasons": ["官方交易限制具有否決權"],
                "reason_code": "recommendation_safety_hard_block",
                "source": "shared_project_referee",
                "version": "referee-v1",
            },
            "blocked",
            True,
        ),
        (
            {
                "decision_ready": False,
                "main_status": "不判斷",
                "main_reasons": ["官方確認當日無交易"],
                "reason_code": "no_trade",
                "source": "shared_project_referee",
                "version": "referee-v1",
            },
            "blocked",
            True,
        ),
        (
            {
                "decision_ready": False,
                "main_status": "資料不足",
                "main_reasons": ["同日技術指標尚未通過資料品質檢查"],
                "reason_code": "technical_not_ready",
                "source": "shared_project_referee",
                "version": "referee-v1",
            },
            "insufficient_data",
            False,
        ),
    ],
)
def test_canonical_close_batch_wrapper_owns_analysis_metadata(
    referee: dict[str, object],
    expected_status: str,
    expected_complete: bool,
) -> None:
    implementation_payload = {
        "ok": True,
        "status": "volume_mismatch",
        "reason": "single-day microstructure is not referee eligible",
        "code": "2454",
        "trade_date": "2026-08-27",
        "data_quality": {"decision_ready": False},
        "referee": referee,
        # A legacy/inner implementation must not be able to spoof wrapper metadata.
        "analysis_contract_version": "legacy-contract",
        "analysis_status": {"status": "legacy-status"},
        "microstructure_status": {"status": "legacy-status"},
    }

    with patch.object(
        canonical_service,
        "_build_canonical_close_batch_snapshot_impl",
        return_value=implementation_payload,
    ) as implementation:
        result = canonical_service.build_canonical_close_batch_snapshot(
            "2454",
            trade_date="2026-08-27",
            include_levels=False,
            level_limit=25,
            analysis_mode="close_batch",
            allow_live_quote_fetch=False,
        )

    implementation.assert_called_once_with(
        "2454",
        trade_date="2026-08-27",
        include_levels=False,
        level_limit=25,
        analysis_mode="close_batch",
        allow_live_quote_fetch=False,
    )
    assert result["analysis_contract_version"] == CONTRACT_VERSION
    assert result["ok"] is expected_complete
    assert result["status"] == expected_status
    assert result["reason"] == "；".join(referee["main_reasons"])
    assert result["analysis_status"] == {
        "status": expected_status,
        "complete": expected_complete,
        "decision_ready": bool(referee["decision_ready"]),
        "main_status": referee["main_status"],
        "main_reasons": referee["main_reasons"],
        "reason_code": referee["reason_code"],
        "source": referee["source"],
        "version": referee["version"],
    }
    # Microstructure quality remains separately visible and cannot downgrade a
    # completed canonical referee verdict by changing the wrapper status.
    assert result["microstructure_status"] == {
        "status": "volume_mismatch",
        "reason": "single-day microstructure is not referee eligible",
        "decision_ready": False,
    }


def test_legacy_component_builder_is_not_used_as_the_canonical_wrapper() -> None:
    component = {
        "ok": False,
        "status": "volume_mismatch",
        "referee": {"main_status": "中性"},
    }
    with patch.object(
        canonical_service,
        "_build_canonical_close_batch_snapshot_impl",
        return_value=component,
    ) as implementation:
        result = canonical_service.build_bot_daily_market_data(
            "2454",
            trade_date="2026-08-27",
            include_levels=False,
            level_limit=30,
            analysis_mode="close_batch",
            allow_live_quote_fetch=False,
        )

    assert result is component
    implementation.assert_called_once_with(
        "2454",
        trade_date="2026-08-27",
        include_levels=False,
        level_limit=30,
        analysis_mode="close_batch",
        allow_live_quote_fetch=False,
    )


def test_web_publishable_paths_do_not_execute_legacy_verdict_algorithms() -> None:
    list_source = inspect.getsource(app._build_row_uncached)
    detail_source = inspect.getsource(app.api_stock_detail)
    readiness_source = inspect.getsource(app.data_readiness_for_items)
    forbidden_calls = (
        "score_stock_cached(",
        "classify_practical_status_cached(",
        "calc_support_resistance(",
        "calc_support_resistance_detail(",
        "synthesize_next_day_outlook(",
        "futures_night_signal_for_stock(",
        "yfinance_quote(",
        "latest_price_volume_summary(",
    )

    for call in forbidden_calls:
        assert call not in list_source
        assert call not in detail_source
        assert call not in readiness_source
    assert "build_canonical_close_batch_snapshot(" in list_source
    assert "build_canonical_close_batch_snapshot(" in readiness_source


def test_web_support_ui_exposes_only_canonical_zones() -> None:
    detail_html = (REVIEW_SRC / "static" / "detail.html").read_text(encoding="utf-8")
    index_html = (REVIEW_SRC / "static" / "index.html").read_text(encoding="utf-8")

    assert "['正式支撐區', d.support_display" in detail_html
    assert "['正式賣壓區', d.resistance_display" in detail_html
    assert "`正式支撐區 ${esc(cleanCell(r.support_display" in index_html
    assert "`正式賣壓區 ${esc(cleanCell(r.resistance_display" in index_html


@pytest.mark.parametrize(
    ("case_name", "referee", "trading_state", "expected_analysis_status"),
    [
        (
            "hard-block",
            {
                "decision_ready": False,
                "main_status": "不判斷",
                "main_reasons": ["目前列入官方注意股票"],
                "reason_code": "recommendation_safety_hard_block",
                "source": "shared_project_referee",
                "version": "referee-v1",
                "recommendation_safety": {
                    "status": "restricted",
                    "hard_blocked": True,
                },
            },
            {"status": "normal_trade"},
            "blocked",
        ),
        (
            "no-trade",
            {
                "decision_ready": False,
                "main_status": "不判斷",
                "main_reasons": ["官方日報確認當日無交易量"],
                "reason_code": "no_trade",
                "source": "shared_project_referee",
                "version": "referee-v1",
            },
            {"status": "no_trade", "reason": "official daily report no trade"},
            "blocked",
        ),
        (
            "small-company",
            {
                "decision_ready": True,
                "main_status": "警戒",
                "main_reasons": ["公司規模風險，只保留觀察"],
                "reason_code": None,
                "source": "shared_project_referee",
                "version": "referee-v1",
                "input_assembler_version": "support-resistance-v1",
                "recommendation_safety": {
                    "status": "small_company_risk",
                    "hard_blocked": False,
                    "auto_entry_eligible": False,
                },
                "support_zone": {"zone_low": 95.0, "zone_high": 97.0},
                "resistance_zone": {"zone_low": 105.0, "zone_high": 108.0},
                "component_freshness": {
                    "institution": {
                        "ready": True,
                        "source_date": "2026-08-27",
                    },
                    "margin": {
                        "ready": True,
                        "source_date": "2026-08-27",
                    },
                },
            },
            {"status": "normal_trade"},
            "ready",
        ),
        (
            "data-insufficient",
            {
                "decision_ready": False,
                "main_status": "資料不足",
                "main_reasons": ["同日技術指標尚未通過資料品質檢查"],
                "reason_code": "technical_not_ready",
                "source": "shared_project_referee",
                "version": "referee-v1",
            },
            {"status": "normal_trade"},
            "insufficient_data",
        ),
    ],
)
def test_web_projection_cannot_override_canonical_referee_states(
    case_name: str,
    referee: dict[str, object],
    trading_state: dict[str, object],
    expected_analysis_status: str,
) -> None:
    del case_name
    canonical_payload = {"referee": referee}
    snapshot = {
        "trade_date": "2026-08-27",
        "analysis_contract_version": CONTRACT_VERSION,
        "analysis_status": canonical_service._canonical_analysis_status(canonical_payload),
        "trading_state": trading_state,
        "referee": referee,
    }
    conflicting_legacy_projection = {
        "main_status": "可觀察",
        "main_reasons": ["legacy classifier says buy"],
        "decision_ready": True,
        "reason_code": "legacy",
        "support_display": "legacy support",
        "resistance_display": "legacy resistance",
        "can_be_overridden_by_model": True,
    }

    projected = app._canonicalize_web_practical_status(
        conflicting_legacy_projection,
        snapshot,
        current_price=100.0,
    )

    assert projected["main_status"] == referee["main_status"]
    assert projected["main_reasons"] == referee["main_reasons"]
    assert projected["decision_ready"] is bool(referee["decision_ready"])
    assert projected["reason_code"] == referee["reason_code"]
    assert projected["referee_source"] == referee["source"]
    assert projected["referee_version"] == referee["version"]
    assert projected["recommendation_safety"] == referee.get("recommendation_safety", {})
    assert projected["trading_state"] == trading_state
    assert projected["analysis_contract_version"] == CONTRACT_VERSION
    assert projected["analysis_status"]["status"] == expected_analysis_status
    assert projected["can_be_overridden_by_model"] is False

    component_freshness = referee.get("component_freshness", {})
    institution = component_freshness.get("institution", {})
    margin = component_freshness.get("margin", {})
    assert projected["institution_date"] == institution.get("source_date")
    assert projected["margin_date"] == margin.get("source_date")
    assert projected["chip_components_current"] is bool(
        institution.get("ready") and margin.get("ready")
    )


def test_historical_external_event_reader_excludes_later_effective_trade_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MutableClock:
        current = datetime(2026, 8, 27, 8, 0, tzinfo=ZoneInfo("Asia/Taipei"))

        @classmethod
        def now(cls):
            return cls.current

    monkeypatch.setattr(external_event_repository, "datetime", MutableClock)
    conn = _connection()
    base = {
        "event_date": "2026-08-26",
        "published_at": "2026-08-26T18:00:00+08:00",
        "code": "2454",
        "source_id": "MOPS",
        "publisher": "公開資訊觀測站",
        "source_url": "https://example.com/filing",
        "source_class": "official_filing",
        "event_type": "material_event",
        "summary_excerpt": "測試事件",
        "direction": "unknown",
        "confidence": "medium",
        "time_horizon": "short",
        "affected_terms": ["聯發科"],
        "quality_status": "ok",
        "source_quality": "official",
        "license_class": "official_open_data",
        "analysis_version": "test-v1",
        "reliability_score": 1.0,
        "reference_value_score": 0.9,
    }
    upsert_external_market_events(
        conn,
        [{**base, "event_key": "available", "title": "開盤前已可用"}],
    )
    MutableClock.current = datetime(2026, 8, 27, 18, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    upsert_external_market_events(
        conn,
        [{**base, "event_key": "future", "title": "收盤後才抓到"}],
    )

    found = read_stock_external_market_events(
        conn,
        code="2454",
        reference_date="2026-08-26",
        stock_terms=["聯發科"],
    )

    assert [row["event_key"] for row in found] == ["available"]
    assert found[0]["available_at"] == "2026-08-27T08:00:00+08:00"
    assert found[0]["effective_tw_trade_date"] == "2026-08-27"


def test_historical_news_reader_excludes_later_effective_trade_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MutableClock:
        current = datetime(2026, 8, 27, 8, 0, tzinfo=ZoneInfo("Asia/Taipei"))

        @classmethod
        def now(cls):
            return cls.current

    monkeypatch.setattr(news_radar_repository, "datetime", MutableClock)
    conn = _connection()
    base = {
        "event_date": "2026-08-26",
        "published_at": "2026-08-26T18:00:00+08:00",
        "source_id": "GDELT",
        "publisher": "新聞索引",
        "source_url": "https://example.com/news",
        "affected_terms": ["聯發科"],
        "quality_status": "ok",
        "license_class": "metadata_only",
        "analysis_version": "test-v1",
        "reliability_score": 0.5,
        "reference_value_score": 0.5,
    }
    upsert_news_radar_events(
        conn,
        [{**base, "event_key": "available", "title": "聯發科開盤前索引"}],
    )
    MutableClock.current = datetime(2026, 8, 27, 18, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    upsert_news_radar_events(
        conn,
        [{**base, "event_key": "future", "title": "聯發科收盤後索引"}],
    )

    found = read_stock_news_radar_events(
        conn,
        reference_date="2026-08-26",
        stock_terms=["聯發科"],
    )

    assert [row["event_key"] for row in found] == ["available"]
    assert found[0]["available_at"] == "2026-08-27T08:00:00+08:00"
    assert found[0]["effective_tw_trade_date"] == "2026-08-27"


def test_historical_canonical_snapshot_excludes_future_official_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "pit.db"
    conn = sqlite3.connect(database)
    conn.executescript(
        """
        CREATE TABLE history_price (
            date TEXT, code TEXT, open REAL, high REAL, low REAL, close REAL,
            volume REAL, amount REAL, source TEXT, source_quality TEXT,
            market TEXT, volume_unit TEXT
        );
        CREATE TABLE official_company_event (
            code TEXT, disclosed_date TEXT, disclosed_time TEXT, fact_date TEXT,
            subject TEXT, explanation TEXT, article_code TEXT,
            attention_level TEXT, source TEXT, source_quality TEXT, fetched_at TEXT,
            available_at TEXT, market_session TEXT, effective_tw_trade_date TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO history_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "2026-08-20", "2454", 100, 105, 99, 103, 1000000, 103000000,
            "TWSE", "official", "listed", "shares",
        ),
    )
    conn.executemany(
        "INSERT INTO official_company_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "2454", "2026-08-20", "120000", "2026-08-20", "當日事件",
                "可於當日使用", "1", "normal", "MOPS", "official", "2026-08-20T12:00:00+08:00",
                "2026-08-20T12:00:00+08:00", "intraday", "2026-08-21",
            ),
            (
                "2454", "2026-08-26", "120000", "2026-08-26", "未來事件",
                "歷史快照不可看到", "1", "normal", "MOPS", "official", "2026-08-26T12:00:00+08:00",
                "2026-08-26T12:00:00+08:00", "intraday", "2026-08-27",
            ),
            (
                "2454", "2026-08-20", "120000", "2026-08-20", "同日稍後事件",
                "disclosed_date 相同但 available_at 晚於 cutoff", "1", "normal",
                "MOPS", "official", "2026-08-20T14:00:00+08:00",
                "2026-08-20T14:00:00+08:00", "post_market", "2026-08-21",
            ),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(market_microstructure_repository, "_database_path", lambda: database)
    monkeypatch.setattr(
        market_microstructure_repository,
        "resolve_full_market_analysis_date",
        lambda _conn, requested=None: requested or "2026-08-20",
    )

    snapshot = market_microstructure_repository.read_daily_market_microstructure(
        "2454",
        "2026-08-20",
        analysis_cutoff="2026-08-20T13:00:00+08:00",
    )

    assert [row["subject"] for row in snapshot["official_event_rows"]] == ["當日事件"]
