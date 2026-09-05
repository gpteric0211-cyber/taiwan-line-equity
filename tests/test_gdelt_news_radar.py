from __future__ import annotations

from datetime import datetime
import sqlite3
from zoneinfo import ZoneInfo

from adapter import gdelt_news
from services import bot_market_data_service, line_bot_service
from repository import news_radar_repository
from repository.news_radar_repository import read_stock_news_radar_events, upsert_news_radar_events


def test_gdelt_article_keeps_metadata_only_and_maps_company_alias() -> None:
    row = gdelt_news._normalize_article(
        {
            "url": "https://example.com/news/tsmc",
            "title": "TSMC discusses new semiconductor investment",
            "seendate": "20260826T021500Z",
            "domain": "example.com",
            "language": "English",
            "sourcecountry": "United States",
        },
        "TSMC",
    )
    assert row is not None
    assert row["event_date"] == "2026-08-26"
    assert row["source_quality"] == "supplemental"
    assert row["verification_status"] == "unverified"
    assert row["license_class"] == "gdelt_open_metadata_citation_required"
    assert "台積電" in row["affected_terms"]
    assert "summary_excerpt" not in row
    assert row["can_override_main_status"] is False


def test_gdelt_rate_limit_is_source_delayed_without_retry(monkeypatch) -> None:
    class FakeResponse:
        status_code = 429

    calls = []
    monkeypatch.setattr(
        gdelt_news.requests,
        "get",
        lambda *_args, **_kwargs: calls.append(1) or FakeResponse(),
    )
    result = gdelt_news.fetch_gdelt_news_radar()
    assert result["ok"] is False
    assert result["status"] == "source_delayed"
    assert result["sources"][0]["reason"] == "rate_limited"
    assert result["sources"][0]["attempts"] == 1
    assert len(calls) == 1


def test_gdelt_connection_timeout_retries_then_succeeds(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        text = '{"articles":[]}'

        @staticmethod
        def raise_for_status():
            return None

    responses = iter([gdelt_news.requests.ConnectTimeout("slow"), FakeResponse()])

    def fake_get(*_args, **_kwargs):
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    sleeps = []
    monkeypatch.setattr(gdelt_news.requests, "get", fake_get)
    monkeypatch.setattr(gdelt_news.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = gdelt_news.fetch_gdelt_news_radar(
        max_attempts=3,
        retry_backoff_seconds=0.25,
    )

    assert result["ok"] is True
    assert result["sources"][0]["attempts"] == 2
    assert sleeps == [0.25]


def test_gdelt_success_response_is_normalized(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        text = '{"articles":[{"url":"https://example.com/a","title":"TSMC chip update","seendate":"20260826T021500Z","domain":"example.com"}]}'

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(gdelt_news.requests, "get", lambda *_args, **_kwargs: FakeResponse())
    result = gdelt_news.fetch_gdelt_news_radar()
    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["rows"] == 1
    assert result["items"][0]["verification_status"] == "unverified"


def test_news_radar_context_is_visible_but_never_referee_ready(monkeypatch) -> None:
    class FakeDateTime:
        @staticmethod
        def now(_tz):
            return datetime(2026, 8, 26, 12, 0, tzinfo=ZoneInfo("Asia/Taipei"))

    monkeypatch.setattr(bot_market_data_service, "datetime", FakeDateTime)
    context = bot_market_data_service._news_radar_context_payload(
        {
            "news_radar_rows": [{
                "event_key": "e1",
                "event_date": "2026-08-26",
                "published_at": "2026-08-26T10:00:00+08:00",
                "publisher": "example.com",
                "source_url": "https://example.com/a",
                "title": "TSMC chip story",
                "matched_stock_terms": ["台積電"],
                "metrics": {"potential_direction": "positive"},
                "quality_status": "ok",
                "source_quality": "supplemental",
                "verification_status": "unverified",
                "content_fingerprint": "fp1",
            }]
        }
    )
    assert context["available"] is True
    assert context["status"] == "unverified"
    assert context["ready_for_referee"] is False
    assert context["can_override_main_status"] is False


def test_news_radar_repository_maps_exact_stock_term_without_referee_permission(monkeypatch) -> None:
    class FakeDateTime:
        @staticmethod
        def now():
            return datetime(2026, 8, 26, 12, 0, tzinfo=ZoneInfo("Asia/Taipei"))

    monkeypatch.setattr(news_radar_repository, "datetime", FakeDateTime)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    row = gdelt_news._normalize_article(
        {
            "url": "https://example.com/news/foxconn",
            "title": "Foxconn expands server production",
            "seendate": "20260826T021500Z",
            "domain": "example.com",
        },
        "Foxconn",
    )
    assert row is not None
    upsert_news_radar_events(conn, [row])
    found = read_stock_news_radar_events(
        conn,
        reference_date="2026-08-26",
        stock_terms=["鴻海", "伺服器"],
    )
    assert len(found) == 1
    assert "鴻海" in found[0]["matched_stock_terms"]
    assert found[0]["verification_status"] == "unverified"
    assert found[0]["can_override_main_status"] is False


def test_manual_claim_parser_splits_claims_and_removes_url() -> None:
    parsed = line_bot_service._manual_claim_request(
        "查證 2317：https://www.cmoney.tw/forum/stock/2317 營收年增54%，而且取得新訂單。股價一定漲"
    )
    assert parsed is not None
    assert parsed["url"].startswith("https://www.cmoney.tw/")
    assert parsed["claims"] == ["營收年增54%", "取得新訂單", "股價一定漲"]


def test_manual_claim_reply_has_official_check_and_price_causality_warning(monkeypatch) -> None:
    fixed = datetime(2026, 8, 26, 11, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    monkeypatch.setattr(line_bot_service, "now_tpe", lambda: fixed)
    answer = line_bot_service._format_manual_claim_verification(
        facts={
            "code": "2317",
            "stock": {"name": "鴻海"},
            "intraday_quote": {"available": True, "price": "247", "open": "243", "high": "248", "low": "241.5"},
            "official_ohlcv": {"close": "243"},
            "external_event_context": {"events": [{
                "event_date": "2026-08-17",
                "published_at": "2026-08-17T18:00:00+08:00",
                "publisher": "官方市場資料",
                "title": "鴻海 2026-07 月營收",
                "summary_excerpt": "官方月營收",
                "metrics": {"year_over_year_pct": 54.189},
            }]},
            "official_event_context": {"events": []},
        },
        request={"claims": ["鴻海營收年增 54.19%，一定會漲"], "url": None},
    )
    assert "事實部分已證實，但漲跌預測未獲證實" in answer
    assert "現價 247、昨收 243" in answer
    assert "不能證明是該貼文造成" in answer
    assert answer.endswith("僅供資料整理，不構成投資建議。")


def test_answer_stock_question_routes_manual_claim_to_deterministic_verifier(monkeypatch) -> None:
    fixed = datetime(2026, 8, 26, 11, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    monkeypatch.setattr(line_bot_service, "now_tpe", lambda: fixed)
    monkeypatch.setattr(
        line_bot_service,
        "resolve_stock_query",
        lambda _query: {"ok": True, "stock": {"code": "2317", "name": "鴻海"}},
    )
    monkeypatch.setattr(
        line_bot_service,
        "fetch_daily_market_data",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "code": "2317",
            "trade_date": "2026-08-25",
            "stock": {"name": "鴻海", "market": "listed", "exchange": "TWSE"},
            "freshness": {"ready": True, "status": "ok"},
            "ohlcv": {"official_trusted": True, "close": 243, "source": "TWSE", "source_quality": "OFFICIAL"},
            "intraday_quote": {"available": True, "price": 247, "open": 243, "high": 248, "low": 241.5},
            "technical": {"available": False},
            "valuation": {"available": False},
            "data_quality": {"decision_ready": False},
            "external_event_context": {"events": [{
                "event_date": "2026-08-17",
                "published_at": "2026-08-17T18:00:00+08:00",
                "publisher": "官方市場資料",
                "title": "鴻海 2026-07 月營收",
                "metrics": {"year_over_year_pct": 54.189},
            }]},
            "official_event_context": {"events": []},
        },
    )
    answer = line_bot_service.answer_stock_question("查證 2317：營收年增54.19%，而且股價一定漲")
    assert "社群貼文逐條查證" in answer
    assert "請勿把舊文當今日消息" in answer
    assert "事實已由官方資料證實" in answer
    assert "屬於看法或漲跌預測" in answer
    assert "現價 247、昨收 243" in answer
