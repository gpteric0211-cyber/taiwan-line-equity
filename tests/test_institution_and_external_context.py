from __future__ import annotations

import json
import csv
import io
import pytest
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.official_company_events import _normalize as normalize_event
from adapter.official_external_events import (
    _normalize_revenue as normalize_revenue,
    fetch_authorized_trump_social_feed,
)
from adapter.taifex_night import fetch_taifex_night_snapshot
from adapter.tpex_institution import normalize_tpex_institution_row
from adapter.twse_institution import normalize_twse_institution_row
from analysis.estimated_chip_cost import DailyCostInput, calculate_institution_estimated_cost
from services.bot_market_data_service import (
    _external_event_context_payload,
    _official_event_context_payload,
    _taifex_night_context_payload,
)


def test_twse_and_tpex_gross_institution_fields_are_mapped_and_reconcile():
    twse_raw = ["2330", "台積電", "120", "20", "100", "0", "0", "0", "50", "10", "40", "3", "8", "3", "5", "4", "6", "-2", "143"]
    twse = normalize_twse_institution_row([], twse_raw, "2026-08-21")
    assert twse is not None
    assert twse["foreign_buy"] - twse["foreign_sell"] == twse["foreign_net"]
    assert twse["trust_buy"] - twse["trust_sell"] == twse["trust_net"]
    assert twse["dealer_buy"] - twse["dealer_sell"] == twse["dealer_net"]

    tpex_raw = ["6488", "環球晶", "10", "2", "8", "0", "0", "0", "110", "20", "90", "30", "5", "25", "4", "1", "3", "2", "1", "1", "6", "2", "4", "119"]
    tpex = normalize_tpex_institution_row([], tpex_raw, "2026-08-21")
    assert tpex is not None
    assert tpex["foreign_buy"] - tpex["foreign_sell"] == tpex["foreign_net"]
    assert tpex["trust_buy"] - tpex["trust_sell"] == tpex["trust_net"]
    assert tpex["dealer_buy"] - tpex["dealer_sell"] == tpex["dealer_net"]


def test_foreign_incremental_cost_does_not_seed_from_total_holding_anchor():
    rows = [
        DailyCostInput("2330", "2026-08-20", 100, 1000, 100000, foreign_net=100, institution_source_quality="official", foreign_holding_shares=9_000_000),
        DailyCostInput("2330", "2026-08-21", 110, 1000, 110000, foreign_net=100, institution_source_quality="official", foreign_holding_shares=9_100_000),
    ]
    result = calculate_institution_estimated_cost(rows, "foreign_estimated")[-1]
    assert result["estimated_cost"] == 105.0
    assert result["formula_version"] == "official_net_flow_incremental_inventory_v3"
    assert result["position_shares"] == 200.0


def test_official_event_normalization_and_attention_gate():
    row = {
        "發言日期": "1150824",
        "發言時間": "153000",
        "公司代號": "2330",
        "公司名稱": "台積電",
        "主旨 ": "公告重大火災事件",
        "事實發生日": "1150824",
        "說明": "依規定公告。",
    }
    event = normalize_event(row, "listed")
    assert event is not None
    assert event["disclosed_date"] == "2026-08-24"
    assert event["disclosed_time"] == "153000"
    assert event["attention_level"] == "attention"


def test_mops_disclosure_time_is_zero_padded_for_correct_chronology():
    event = normalize_event(
        {
            "發言日期": "1150824",
            "發言時間": "70003",
            "公司代號": "2330",
            "公司名稱": "台積電",
            "主旨": "公告測試",
        },
        "listed",
    )
    assert event is not None
    assert event["disclosed_time"] == "070003"


@pytest.mark.parametrize("prefix", ["", "\ufeff"])
@pytest.mark.parametrize("wire_format", ["json", "csv"])
def test_taifex_snapshot_uses_latest_liquid_after_hours_contract(monkeypatch, prefix, wire_format):
    payload = [
        {"Date": "20260821", "Contract": "TX", "ContractMonth(Week)": "202608", "Last": "25000", "%": "0.6", "Volume": "10000", "TradingSession": "盤後"},
        {"Date": "20260821", "Contract": "TX", "ContractMonth(Week)": "202609", "Last": "24900", "%": "0.4", "Volume": "500", "TradingSession": "盤後"},
        {"Date": "20260821", "Contract": "TE", "ContractMonth(Week)": "202608", "Last": "1400", "%": "0.8", "Volume": "1000", "TradingSession": "盤後"},
    ]

    wire=json.dumps(payload, ensure_ascii=False)
    if wire_format=="csv":
        columns={"Date":"日期","Contract":"契約代號","ContractMonth(Week)":"到期月份(週別)","Last":"最後成交價","%":"漲跌%","Volume":"合計成交量","TradingSession":"交易時段"}
        stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=list(columns.values()))
        writer.writeheader();writer.writerows({columns[key]:value for key,value in row.items()} for row in payload)
        wire=stream.getvalue()
    class Response:
        text = prefix + wire
        encoding = "utf-8"

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr("adapter.taifex_night.requests.get", lambda *args, **kwargs: Response())
    result = fetch_taifex_night_snapshot()
    assert result["ok"] is True
    tx = next(row for row in result["items"] if row["contract"] == "TX")
    assert tx["contract_month"] == "202608"


def test_night_and_event_contexts_are_non_overriding(monkeypatch):
    night = _taifex_night_context_payload(
        {"taifex_night_rows": [{"trade_date": "2026-08-21", "contract": "TX", "change_pct": 0.8, "volume": 10000, "source_quality": "official"}]},
        "2026-08-21",
        "2330",
    )
    assert night["available"] is True
    assert night["stance"] == "positive"
    assert night["can_override_main_status"] is False

    class FakeDateTime:
        @staticmethod
        def now(_tz):
            from datetime import datetime

            return datetime.fromisoformat("2026-08-24T12:00:00+08:00")

    monkeypatch.setattr("services.bot_market_data_service.datetime", FakeDateTime)
    events = _official_event_context_payload(
        {"official_event_rows": [{"disclosed_date": "2026-08-23", "subject": "董事會決議", "explanation": "公告內容", "attention_level": "normal", "source_quality": "official"}]}
    )
    assert events["available"] is True
    assert events["can_override_main_status"] is False

    conference = _official_event_context_payload(
        {"official_event_rows": [{"disclosed_date": "2026-08-23", "subject": "法人說明會",
                                  "explanation": "說明營運成果與展望", "attention_level": "normal",
                                  "source_quality": "official"}]}
    )
    assert conference["events"][0]["event_type"] == "investor_conference"
    assert conference["events"][0]["explanation_excerpt"] == "說明營運成果與展望"


def test_stale_night_context_is_excluded():
    night = _taifex_night_context_payload(
        {"taifex_night_rows": [{"trade_date": "2026-08-19", "contract": "TX", "change_pct": 1.2, "volume": 10000, "source_quality": "official"}]},
        "2026-08-21",
        "2330",
    )
    assert night["available"] is False
    assert night["status"] == "source_delayed"
    assert night["can_override_main_status"] is False


def test_official_monthly_revenue_is_quantitatively_classified():
    row = {
        "出表日期": "1150825",
        "資料年月": "11507",
        "公司代號": "2330",
        "公司名稱": "台積電",
        "營業收入-當月營收": "300000000",
        "營業收入-上月比較增減(%)": "12",
        "營業收入-去年同月增減(%)": "25",
        "累計營業收入-當月累計營收": "1800000000",
        "累計營業收入-前期比較增減(%)": "15",
    }
    event = normalize_revenue(row, "listed", "https://openapi.twse.com.tw/example")
    assert event is not None
    assert event["event_date"] == "2026-08-25"
    assert event["direction"] == "positive"
    assert event["confidence"] == "high"
    assert event["reliability_score"] >= 0.8
    assert event["reference_value_score"] >= 0.9
    assert event["can_override_main_status"] is False


def test_external_event_context_prefers_newer_and_excludes_low_reliability(monkeypatch):
    class FakeDateTime:
        @staticmethod
        def now(_tz):
            from datetime import datetime

            return datetime.fromisoformat("2026-08-26T12:00:00+08:00")

    monkeypatch.setattr("services.bot_market_data_service.datetime", FakeDateTime)
    base = {
        "source_id": "MOEA_NEWS",
        "source_quality": "official",
        "quality_status": "ok",
        "event_type": "government_policy",
        "code": None,
        "publisher": "經濟部",
        "source_url": "https://www.moea.gov.tw/example",
        "direction": "positive",
        "confidence": "medium",
        "reference_value_score": 0.75,
        "reliability_score": 0.97,
        "matched_stock_terms": ["半導體"],
        "mapping_method": "official_industry_taxonomy",
        "metrics": {},
    }
    context = _external_event_context_payload(
        {
            "external_event_rows": [
                {**base, "event_key": "new", "content_fingerprint": "new", "event_date": "2026-08-26", "published_at": "2026-08-26T10:00:00+08:00", "title": "新政策"},
                {**base, "event_key": "old", "content_fingerprint": "old", "event_date": "2026-08-10", "published_at": "2026-08-10T10:00:00+08:00", "title": "舊政策"},
                {**base, "event_key": "rumor", "content_fingerprint": "rumor", "event_date": "2026-08-26", "published_at": "2026-08-26T11:00:00+08:00", "title": "低可靠事件", "reliability_score": 0.4},
            ]
        }
    )
    assert context["available"] is True
    assert [event["title"] for event in context["events"]] == ["新政策"]
    assert context["quality_contract"]["topic_latest_wins"] is True
    assert context["quality_contract"]["time_decay_applied"] is True
    assert context["can_override_main_status"] is False


def test_truth_social_direct_url_is_rejected_even_when_configured(monkeypatch):
    monkeypatch.setenv("TRUMP_SOCIAL_AUTHORIZED_FEED_URL", "https://truthsocial.com/@realDonaldTrump")
    monkeypatch.setenv("TRUMP_SOCIAL_LICENSE_REFERENCE", "example-license")
    result = fetch_authorized_trump_social_feed()
    assert result["ok"] is False
    assert result["sources"][0]["reason"] == "direct_or_unsafe_feed_not_allowed"

def test_taifex_rejects_html_or_unknown_schema_instead_of_empty_success():
    from adapter.taifex_night import _report_rows
    with pytest.raises(ValueError):
        _report_rows("<html>Service unavailable</html>")
