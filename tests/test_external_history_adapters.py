from __future__ import annotations

import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from adapter import global_market_history
from adapter import official_valuation_history
from adapter.official_valuation_history import parse_tpex_daily_pe_payload
from adapter.taifex_night_history import parse_taifex_futures_csv


def test_global_history_uses_actual_retrieval_time_and_daily_close(monkeypatch) -> None:
    observed = datetime(2026, 9, 2, 17, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    monkeypatch.setattr(global_market_history, "now_tpe", lambda: observed)
    monkeypatch.setattr(
        global_market_history,
        "request_json",
        lambda *_args, **_kwargs: {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "exchangeTimezoneName": "America/New_York",
                            "currency": "USD",
                        },
                        "timestamp": [1787923800, 1788010200],
                        "indicators": {"quote": [{"close": [100.0, 105.0]}]},
                    }
                ]
            }
        },
    )

    result = global_market_history.fetch_global_market_history_rows("TSM")

    assert result["ok"] is True
    assert result["row_count"] == 2
    assert result["rows"][0]["available_at"] == "2026-09-02T17:00:00+08:00"
    assert result["rows"][1]["previous_close"] == 100.0
    assert result["rows"][1]["change_pct"] == pytest.approx(5.0)
    assert result["rows"][1]["source_quality"] == "supplemental"


def test_global_history_excludes_the_current_unfinished_exchange_session(
    monkeypatch,
) -> None:
    observed = datetime(2026, 9, 2, 21, 45, tzinfo=ZoneInfo("Asia/Taipei"))
    exchange_timezone = ZoneInfo("America/New_York")
    completed_timestamp = int(
        datetime(2026, 9, 1, 9, 30, tzinfo=exchange_timezone).timestamp()
    )
    unfinished_timestamp = int(
        datetime(2026, 9, 2, 9, 30, tzinfo=exchange_timezone).timestamp()
    )
    monkeypatch.setattr(global_market_history, "now_tpe", lambda: observed)
    monkeypatch.setattr(
        global_market_history,
        "request_json",
        lambda *_args, **_kwargs: {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "exchangeTimezoneName": "America/New_York",
                            "currency": "USD",
                        },
                        "timestamp": [completed_timestamp, unfinished_timestamp],
                        "indicators": {"quote": [{"close": [100.0, 111.0]}]},
                    }
                ]
            }
        },
    )

    result = global_market_history.fetch_global_market_history_rows("TSM")

    assert result["ok"] is True
    assert result["row_count"] == 1
    assert result["max_date"] == "2026-09-01"
    assert result["rows"][0]["close"] == 100.0
    assert result["incomplete_session_rows_skipped"] == 1


def _taifex_row(
    *,
    trade_date: str,
    contract: str,
    month: str,
    last: str,
    change_pct: str,
    volume: str,
    session: str,
) -> list[str]:
    row = [""] * 18
    row[0] = trade_date
    row[1] = contract
    row[2] = month
    row[6] = last
    row[8] = change_pct
    row[9] = volume
    row[17] = session
    return row


def test_taifex_parser_keeps_after_hours_highest_volume_outright_month() -> None:
    rows = [
        [f"column_{index}" for index in range(18)],
        _taifex_row(
            trade_date="2026/08/28",
            contract="TX",
            month="202609",
            last="24,000",
            change_pct="1.2%",
            volume="12,000",
            session="盤後",
        ),
        _taifex_row(
            trade_date="2026/08/28",
            contract="TX",
            month="202610",
            last="23,900",
            change_pct="1.0%",
            volume="100",
            session="盤後",
        ),
        _taifex_row(
            trade_date="2026/08/28",
            contract="TX",
            month="202609/202610",
            last="100",
            change_pct="0.1%",
            volume="50,000",
            session="盤後",
        ),
        _taifex_row(
            trade_date="2026/08/28",
            contract="TX",
            month="202609",
            last="23,500",
            change_pct="-1%",
            volume="90,000",
            session="一般",
        ),
    ]
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    content = stream.getvalue().encode("big5")

    parsed = parse_taifex_futures_csv(content)

    assert parsed == [
        {
            "trade_date": "2026-08-28",
            "contract": "TX",
            "contract_month": "202609",
            "last": 24000.0,
            "change_pct": 1.2,
            "volume": 12000.0,
            "trading_session": "盤後",
            "source": "TAIFEX_FUT_DATA_DOWN",
            "source_quality": "official",
        }
    ]


def test_tpex_parser_maps_official_daily_valuation_fields() -> None:
    payload = {
        "tables": [
            {
                "data": [
                    ["6669", "緯穎", "25.5", "12.0", "114", "1.8", "5.2", "115Q2"],
                    ["invalid", "bad", "1", "1", "1", "1", "1", "1"],
                ]
            }
        ]
    }

    rows = parse_tpex_daily_pe_payload(
        payload,
        requested_date="2026-08-28",
        observed_at="2026-09-02T17:00:00+08:00",
    )

    assert rows == [
        {
            "data_date": "2026-08-28",
            "symbol": "6669",
            "name": "緯穎",
            "close_price": None,
            "dividend_yield": 1.8,
            "dividend_year": "114",
            "pe_ratio": 25.5,
            "pb_ratio": 5.2,
            "financial_year_quarter": "115Q2",
            "source": "TPEX_PERATIO_ANALYSIS",
            "source_status": "ok",
            "updated_at": "2026-09-02 17:00:00",
            "available_at": "2026-09-02T17:00:00+08:00",
            "timezone": "Asia/Taipei",
        }
    ]


def test_tpex_history_retries_a_transient_read_timeout(monkeypatch) -> None:
    calls = 0

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "tables": [
                    {
                        "data": [
                            [
                                "6669",
                                "緯穎",
                                "25.5",
                                "12.0",
                                "114",
                                "1.8",
                                "5.2",
                                "115Q2",
                            ]
                        ]
                    }
                ]
            }

    def fake_get(*_args, **_kwargs) -> FakeResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise official_valuation_history.requests.ReadTimeout("temporary timeout")
        return FakeResponse()

    monkeypatch.setattr(official_valuation_history.requests, "get", fake_get)
    monkeypatch.setattr(official_valuation_history.time, "sleep", lambda _value: None)

    result = official_valuation_history.fetch_tpex_daily_valuation(
        "2026-06-02",
        max_attempts=2,
    )

    assert result["ok"] is True
    assert result["row_count"] == 1
    assert calls == 2


def test_twse_history_rejects_an_exact_date_mismatch(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"stat": "OK", "date": "20260901", "fields": [], "data": []}

    monkeypatch.setattr(
        official_valuation_history.requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(),
    )

    result = official_valuation_history.fetch_twse_daily_valuation("2026-08-28")

    assert result["ok"] is False
    assert "exact-date mismatch" in result["error"]


def test_twse_history_retries_official_burst_throttle(monkeypatch) -> None:
    calls = 0

    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code != 200:
                raise official_valuation_history.requests.HTTPError(
                    f"{self.status_code} throttled",
                    response=self,
                )

        def json(self) -> dict:
            return {
                "stat": "OK",
                "date": "20260828",
                "fields": ["Code", "Name", "ClosePrice"],
                "data": [["2454", "聯發科", "3925"]],
            }

    def fake_get(**_kwargs) -> FakeResponse:
        nonlocal calls
        calls += 1
        return FakeResponse(428 if calls == 1 else 200)

    monkeypatch.setattr(
        official_valuation_history,
        "_throttled_twse_get",
        fake_get,
    )
    monkeypatch.setattr(official_valuation_history.time, "sleep", lambda _value: None)

    result = official_valuation_history.fetch_twse_daily_valuation(
        "2026-08-28",
        max_attempts=2,
    )

    assert result["ok"] is True
    assert result["row_count"] == 1
    assert result["rows"][0]["symbol"] == "2454"
    assert calls == 2


def test_twse_history_retries_empty_json_throttle(monkeypatch) -> None:
    calls = 0

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            nonlocal calls
            if calls == 1:
                raise official_valuation_history.requests.exceptions.JSONDecodeError(
                    "empty response",
                    "",
                    0,
                )
            return {
                "stat": "OK",
                "date": "20260828",
                "fields": ["Code", "Name", "ClosePrice"],
                "data": [["2454", "聯發科", "3925"]],
            }

    def fake_get(**_kwargs) -> FakeResponse:
        nonlocal calls
        calls += 1
        return FakeResponse()

    monkeypatch.setattr(
        official_valuation_history,
        "_throttled_twse_get",
        fake_get,
    )
    monkeypatch.setattr(
        official_valuation_history,
        "_extend_twse_cooldown",
        lambda _value: None,
    )
    monkeypatch.setattr(official_valuation_history.time, "sleep", lambda _value: None)

    result = official_valuation_history.fetch_twse_daily_valuation(
        "2026-08-28",
        max_attempts=2,
    )

    assert result["ok"] is True
    assert result["row_count"] == 1
    assert calls == 2
