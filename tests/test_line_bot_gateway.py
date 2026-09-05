from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import requests
from fastapi.testclient import TestClient


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

# Unit tests must not write to the operator's configured persistent LINE memory.
os.environ.setdefault("LINE_MEMORY_STORAGE", "memory")
os.environ["LINE_MODEL_V2_ROLLOUT"] = "off"
os.environ["LINE_REPLY_TELEMETRY_ENABLED"] = "false"

from adapter import line_messaging  # noqa: E402
from adapter.line_messaging import verify_line_signature  # noqa: E402
from api import line_webhook  # noqa: E402
from core import line_bot_config  # noqa: E402
from line_bot_app import app  # noqa: E402
from services import bot_market_data_service, line_bot_service, model_admission_service  # noqa: E402
from services.line_model_candidate_reply_service import (  # noqa: E402
    render_validated_candidate_reply_preview,
)


def _signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def test_verify_line_signature_uses_raw_body() -> None:
    secret = "test-secret"
    body = b'{"events":[]}'
    signature = _signature(secret, body)
    assert verify_line_signature(body, signature, secret) is True
    assert verify_line_signature(body + b" ", signature, secret) is False


def test_line_bot_env_prefers_project_root_and_falls_back_to_legacy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    primary = tmp_path / ".env.line_bot"
    legacy = tmp_path / "review_src" / ".env.line_bot"
    legacy.parent.mkdir()
    monkeypatch.setattr(line_bot_config, "LINE_BOT_ENV_FILE", primary)
    monkeypatch.setattr(line_bot_config, "LEGACY_LINE_BOT_ENV_FILE", legacy)

    legacy.write_text("LINE_CHANNEL_SECRET=legacy\n", encoding="utf-8")
    assert line_bot_config.resolve_line_bot_env_file() == legacy

    primary.write_text("LINE_CHANNEL_SECRET=primary\n", encoding="utf-8")
    assert line_bot_config.resolve_line_bot_env_file() == primary


def test_close_date_context_never_calls_previous_data_today_after_1500(monkeypatch) -> None:
    fixed = datetime(2026, 8, 24, 16, 10, tzinfo=ZoneInfo("Asia/Taipei"))
    monkeypatch.setattr(line_bot_service, "now_tpe", lambda: fixed)
    monkeypatch.setattr(line_bot_service, "is_taiwan_trading_day", lambda day: day.weekday() < 5)
    monkeypatch.setattr(line_bot_service, "recent_market_date_for_eod", lambda: "2026-08-24")

    context = line_bot_service._market_date_context("廣達收盤多少價格", "2026-08-21")

    assert context["status"] == "source_delayed"
    assert context["label"] == "最近可確認交易日 2026-08-21"
    assert "2026-08-24" in str(context["warning"])
    assert "以下數值屬於 2026-08-21" in str(context["warning"])


def test_yesterday_means_previous_trading_day(monkeypatch) -> None:
    fixed = datetime(2026, 8, 24, 12, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    monkeypatch.setattr(line_bot_service, "now_tpe", lambda: fixed)
    monkeypatch.setattr(line_bot_service, "is_taiwan_trading_day", lambda day: day.weekday() < 5)

    assert line_bot_service._requested_trade_date("京元電子昨天收盤什麼") == "2026-08-21"
    context = line_bot_service._market_date_context("京元電子昨天收盤什麼", "2026-08-21")
    assert context["status"] == "exact"
    assert context["label"] == "前一交易日 2026-08-21"


def test_webhook_verification_accepts_empty_events(monkeypatch) -> None:
    secret = "test-secret"
    monkeypatch.setenv("LINE_CHANNEL_SECRET", secret)
    monkeypatch.setenv("LINE_VERIFY_SIGNATURE", "true")
    body = json.dumps({"events": []}, separators=(",", ":")).encode("utf-8")
    response = TestClient(app).post(
        "/line/webhook",
        content=body,
        headers={"x-line-signature": _signature(secret, body), "content-type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}


def test_webhook_rejects_invalid_signature(monkeypatch) -> None:
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-secret")
    monkeypatch.setenv("LINE_VERIFY_SIGNATURE", "true")
    response = TestClient(app).post(
        "/line/webhook",
        content=b'{"events":[]}',
        headers={"x-line-signature": "invalid", "content-type": "application/json"},
    )
    assert response.status_code == 401


def test_reply_text_uses_verified_standard_https_client(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(line_request, timeout):
        captured["request"] = line_request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("LINE_REPLY_TIMEOUT_SECONDS", "12")
    monkeypatch.setattr(line_messaging.urlrequest, "urlopen", fake_urlopen)

    line_messaging.reply_text("test-reply-token", "測試回覆")

    request = captured["request"]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == line_messaging.LINE_REPLY_URL
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer test-token"
    assert captured["timeout"] == 12
    assert payload == {
        "replyToken": "test-reply-token",
        "messages": [{"type": "text", "text": "測試回覆"}],
    }


def test_get_line_image_content_is_bounded_and_validates_magic(monkeypatch) -> None:
    captured = {}
    png = b"\x89PNG\r\n\x1a\n" + b"safe-image-bytes"

    class FakeResponse:
        headers = {"Content-Length": str(len(png)), "Content-Type": "image/png"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size):
            captured["read_size"] = size
            return png

    def fake_urlopen(line_request, timeout):
        captured["request"] = line_request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(line_messaging.urlrequest, "urlopen", fake_urlopen)
    result = line_messaging.get_line_image_content("123456", max_bytes=1024)

    assert result.data == png
    assert result.mime_type == "image/png"
    assert captured["read_size"] == 1025
    assert captured["request"].full_url.endswith("/123456/content")
    assert captured["request"].get_header("Authorization") == "Bearer test-token"


def test_get_line_image_content_rejects_external_provider_without_request(monkeypatch) -> None:
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(
        line_messaging.urlrequest,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not request")),
    )
    try:
        line_messaging.get_line_image_content("123456", content_provider_type="external")
    except line_messaging.LineMessagingError as exc:
        assert "external" in str(exc)
    else:
        raise AssertionError("external content provider should be rejected")


def test_external_provider_is_rejected_before_credentials_or_request(monkeypatch) -> None:
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(
        line_messaging.urlrequest,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not request")),
    )

    with pytest.raises(line_messaging.LineMessagingError, match="external"):
        line_messaging.get_line_image_content("123456", content_provider_type="external")


def test_stock_resolver_uses_official_master_and_does_not_guess(monkeypatch) -> None:
    monkeypatch.setattr(
        bot_market_data_service,
        "read_active_stock_master_rows",
        lambda: [
            {"code": "2330", "name": "台積電", "market": "listed", "exchange": "TWSE"},
            {"code": "2454", "name": "聯發科", "market": "listed", "exchange": "TWSE"},
        ],
    )
    resolved = bot_market_data_service.resolve_bot_stock_query("聯發科今天 RSI")
    assert resolved["ok"] is True
    assert resolved["stock"]["code"] == "2454"
    missing = bot_market_data_service.resolve_bot_stock_query("不存在公司")
    assert missing["status"] == "not_found"


def test_untrusted_sections_are_removed_before_qwen() -> None:
    facts = line_bot_service._compact_daily(
        {
            "status": "source_delayed",
            "code": "2454",
            "trade_date": "2026-08-21",
            "stock": {"name": "聯發科"},
            "ohlcv": {"official_trusted": True, "close": 1234},
            "technical": {
                "available": True,
                "decision_ready": False,
                "status": "stale",
                "rsi": {"rsi14": 99.99},
            },
            "valuation": {"available": False, "pe_ratio": 88.88},
            "data_quality": {"decision_ready": False, "reason_code": "missing"},
            "price_levels": [{"price": 7777, "volume_lots": 10}],
        }
    )
    assert "rsi" not in facts["technical"]
    assert "pe_ratio" not in facts["valuation"]
    assert "top_volume_levels" not in facts["price_volume"]


def test_numeric_guard_rejects_model_invented_financial_number() -> None:
    facts = {"code": "2454", "trade_date": "2026-08-21", "close": 1234}
    assert line_bot_service._answer_numbers_are_grounded(
        "聯發科 2454 在 2026-08-21 收盤 1234。",
        facts,
        "聯發科資料",
    )
    assert not line_bot_service._answer_numbers_are_grounded(
        "聯發科 2454 目標價 1500。",
        facts,
        "聯發科資料",
    )


def _field_bound_policy_facts() -> dict[str, object]:
    status_claim = "目前判斷：中性；理由：指標多空交錯"
    rsi_claim = "RSI5／RSI10／RSI14：60.55／54.81／53.37"
    return {
        "verified_claims": [status_claim, rsi_claim],
        "valuation": {"available": False},
        "referee": {"decision_ready": True, "main_status": "中性"},
        "answer_contract": {
            "required_claims": [status_claim],
            "required_evidence": [],
            "approved_advisory_claims": [],
        },
        "display": {
            "official_ohlcv": {"close": "100"},
            "technical": {"rsi": {"rsi5": "60.55", "rsi10": "54.81", "rsi14": "53.37"}},
        },
    }


def test_line_policy_rejects_grounded_number_bound_to_wrong_financial_field() -> None:
    facts = _field_bound_policy_facts()
    for wrong_claim in ("RSI14 是 100。", "RSI14：\n100。", "100 被說成 RSI14。"):
        answer = (
            "目前判斷：中性；理由：指標多空交錯。"
            f"{wrong_claim}\n"
            "僅供資料整理，不構成投資建議。"
        )

        assert line_bot_service._answer_numbers_are_grounded(answer, facts, "分析台積電")
        assert not line_bot_service._answer_respects_financial_policy(
            answer,
            facts,
            "分析台積電",
            require_daily_claims=True,
        )
        assert line_bot_service._answer_financial_policy_rejection_reason(
            answer,
            facts,
            "分析台積電",
            require_daily_claims=True,
        ) == "financial_number_not_field_bound"


def test_line_policy_accepts_exact_backend_authored_numeric_claim() -> None:
    facts = _field_bound_policy_facts()
    facts["answer_contract"]["required_claims"].append(
        "RSI5／RSI10／RSI14：60.55／54.81／53.37"
    )
    answer = (
        "目前判斷：中性；理由：指標多空交錯。\n"
        "RSI5／RSI10／RSI14：60.55／54.81／53.37\n"
        "僅供資料整理，不構成投資建議。"
    )

    assert line_bot_service._answer_respects_financial_policy(
        answer,
        facts,
        "分析台積電",
        require_daily_claims=True,
    )


def test_line_policy_rejects_paraphrased_competing_main_conclusion() -> None:
    facts = _field_bound_policy_facts()
    answer = (
        "目前判斷：中性；理由：指標多空交錯。"
        "但整體來說我認為警戒。\n"
        "僅供資料整理，不構成投資建議。"
    )

    assert not line_bot_service._answer_respects_financial_policy(
        answer,
        facts,
        "分析台積電",
        require_daily_claims=True,
    )


def test_line_policy_allows_competing_status_only_inside_approved_advisory() -> None:
    facts = _field_bound_policy_facts()
    advisory = "若收盤跌破支撐 95，風險狀態將降為警戒。"
    facts["answer_contract"]["approved_advisory_claims"] = [advisory]
    answer = (
        "目前判斷：中性；理由：指標多空交錯。\n"
        f"{advisory}\n"
        "僅供資料整理，不構成投資建議。"
    )

    assert line_bot_service._answer_respects_financial_policy(
        answer,
        facts,
        "分析台積電",
        require_daily_claims=True,
    )


def test_line_policy_rejects_provider_or_local_path_disclosure() -> None:
    facts = {"verified_claims": [], "display": {}, "referee": {"decision_ready": False}}
    assert not line_bot_service._answer_respects_financial_policy(
        "資料來源：Yahoo Finance。僅供資料整理，不構成投資建議。",
        facts,
        "資料從哪裡來",
        require_daily_claims=False,
    )
    assert not line_bot_service._answer_respects_financial_policy(
        r"資料庫在 C:\Users\Example\data.sqlite。僅供資料整理，不構成投資建議。",
        facts,
        "資料庫在哪",
        require_daily_claims=False,
    )


def _focused_daily_facts() -> dict[str, object]:
    return {
        "status": "ok",
        "code": "2330",
        "stock": {"name": "台積電", "market": "listed", "exchange": "TWSE"},
        "trade_date": "2026-08-21",
        "freshness": {"status": "current", "ready": True},
        "official_ohlcv": {
            "available": True,
            "official_trusted": True,
            "open": "2375",
            "high": "2410",
            "low": "2365",
            "close": "2410",
            "volume_shares": "18922480",
        },
        "technical": {
            "available": True,
            "decision_ready": True,
            "rsi": {"rsi5": "60.55", "rsi10": "54.81", "rsi14": "53.37"},
            "macd": {"dif": "5.7165", "signal": "3.5161", "oscillator": "2.2005"},
            "moving_averages": {"ma5": "2390", "ma10": "2380", "ma20": "2370", "ma60": "2350"},
            "volume_ma20": "17000000",
        },
        "valuation": {
            "available": True,
            "pe_ratio": "25.5",
            "pb_ratio": "7.1",
            "dividend_yield_pct": "1.8",
        },
        "referee": {
            "decision_ready": True,
            "main_status": "中性",
            "main_reasons": ["指標多空交錯，結構進入區間震盪"],
            "support_zone": {"label": "2340～2405", "strength": "強"},
            "resistance_zone": {"label": "2415～2445", "strength": "強"},
            "can_be_overridden_by_model": False,
        },
        "price_volume": {"decision_ready": False},
        "evidence_summary": {
            "technical_observation": "可用技術證據多空交錯",
            "trend_logic": "收盤、月線與季線排列偏多",
            "momentum_logic": "RSI14 與 MACD 同步偏多",
            "volume_logic": "當日成交量高於 20 日均量",
            "limits": [
                "單日逐筆分價量未通過品質門檻，不納入主動成交力道",
                "缺少同業或歷史估值基準，只能列示估值數值",
            ],
        },
        "verified_claims": [
            "目前判斷：中性；理由：指標多空交錯，結構進入區間震盪",
            "參考區間：支撐 2340～2405（強）／賣壓 2415～2445（強）",
            "官方開／高／低／收：2375／2410／2365／2410",
            "成交量（股）：18922480",
            "RSI5／RSI10／RSI14：60.55／54.81／53.37",
            "MACD DIF／Signal／OSC：5.7165／3.5161／2.2005",
            "MA5／MA10／MA20／MA60：2390／2380／2370／2350",
            "估值 PE／PB／殖利率%：25.5／7.1／1.8",
        ],
    }


def test_model_v2_shadow_request_cannot_change_the_stable_line_reply(monkeypatch) -> None:
    monkeypatch.setattr(
        line_bot_service,
        "resolve_stock_query",
        lambda _query: {"ok": True, "stock": {"code": "2330", "name": "台積電"}},
    )
    monkeypatch.setattr(line_bot_service, "fetch_daily_market_data", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(line_bot_service, "_compact_daily", lambda _payload: _focused_daily_facts())
    monkeypatch.setenv("QWEN_ENABLED", "false")
    questions = (
        "台積電的本益比呢",
        "台積電技術面完整分析",
        "台積電籌碼與風險完整分析",
    )
    monkeypatch.setattr(line_bot_service, "prepare_line_model_shadow", lambda **_kwargs: None)
    stable = {question: line_bot_service.answer_stock_question(question).encode("utf-8") for question in questions}
    monkeypatch.setattr(
        line_bot_service,
        "prepare_line_model_shadow",
        lambda **_kwargs: {"request_id": "shadow-test"},
    )
    shadow_enabled = {
        question: line_bot_service.answer_stock_question(question).encode("utf-8")
        for question in questions
    }

    assert shadow_enabled == stable
    assert b"25.5" in stable["台積電的本益比呢"]

    def fail_shadow(**_kwargs):
        raise RuntimeError("shadow-only failure")

    monkeypatch.setattr(line_bot_service, "prepare_line_model_shadow", fail_shadow)
    after_shadow_failure = line_bot_service.answer_stock_question("台積電的本益比呢").encode("utf-8")

    assert after_shadow_failure == stable["台積電的本益比呢"]


def test_shadow_is_submitted_only_after_successful_line_reply(monkeypatch) -> None:
    order: list[str] = []
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        line_bot_service,
        "_answer_stock_question_result",
        lambda *_args, **_kwargs: line_bot_service._AnswerResult(
            "stable-reply",
            shadow_request={"request_id": "post-reply-job"},
        ),
    )
    monkeypatch.setattr(
        line_bot_service,
        "reply_text",
        lambda _token, text: (order.append("reply"), captured.update(reply=text)),
    )

    def submit(job, *, stable_reply_sha256="", **_kwargs):
        order.append("submit")
        captured["job"] = job
        captured["digest"] = stable_reply_sha256
        return None

    monkeypatch.setattr(line_bot_service, "submit_line_model_shadow", submit)
    event = {
        "webhookEventId": "post-reply-shadow-order",
        "type": "message",
        "replyToken": "reply-post-shadow",
        "source": {"type": "user", "userId": "U-POST-SHADOW"},
        "message": {"type": "text", "text": "台積電完整分析"},
    }

    line_bot_service.handle_line_event(event)

    assert order == ["reply", "submit"]
    assert captured["reply"] == "stable-reply"
    assert captured["job"] == {"request_id": "post-reply-job"}
    assert captured["digest"] == line_bot_service.stable_reply_digest("stable-reply")

    def fail_reply(_token, _text):
        raise line_bot_service.LineMessagingError("reply rejected")

    monkeypatch.setattr(line_bot_service, "reply_text", fail_reply)
    failed_event = {
        **event,
        "webhookEventId": "failed-reply-must-not-submit-shadow",
        "replyToken": "reply-failed-before-shadow",
    }
    line_bot_service.handle_line_event(failed_event)

    assert order == ["reply", "submit"]


def test_contextual_follow_up_reuses_last_stock_and_only_answers_valuation(monkeypatch) -> None:
    resolve_calls: list[str] = []

    def fake_resolve(query: str) -> dict[str, object]:
        resolve_calls.append(query)
        if query == "2330":
            return {"ok": True, "stock": {"code": "2330", "name": "台積電"}}
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    monkeypatch.setattr(line_bot_service, "fetch_daily_market_data", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(line_bot_service, "_compact_daily", lambda _payload: _focused_daily_facts())
    monkeypatch.setenv("QWEN_ENABLED", "false")

    result = line_bot_service._answer_stock_question_result(
        "那本益比呢",
        conversation_context={"code": "2330", "stock_name": "台積電", "last_focus": "overview"},
    )

    assert resolve_calls == ["那本益比呢", "2330"]
    assert result.context_update == {
        "code": "2330",
        "stock_name": "台積電",
        "last_focus": "valuation",
        "last_mode": "stock",
        "trade_date": "2026-08-21",
        "position_state": "",
        "investment_horizon": "",
        "recent_user_questions": ["那本益比呢"],
    }
    assert "台積電（2330）這題我接著說明估值" in result.text
    assert "估值 PE／PB／殖利率%：25.5／7.1／1.8" in result.text
    assert "官方開／高／低／收" not in result.text
    assert "RSI5／RSI10／RSI14" not in result.text


def test_unknown_stock_name_does_not_silently_reuse_previous_stock(monkeypatch) -> None:
    resolve_calls: list[str] = []

    def fake_resolve(query: str) -> dict[str, object]:
        resolve_calls.append(query)
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    answer = line_bot_service.answer_stock_question(
        "不存在公司本益比",
        conversation_context={"code": "2330", "stock_name": "台積電", "last_focus": "overview"},
    )

    assert resolve_calls == ["不存在公司本益比"]
    assert "無法確認你指的是哪一檔股票" in answer


def test_single_short_name_candidate_is_asked_naturally_and_can_be_confirmed(monkeypatch) -> None:
    def fake_resolve(query: str) -> dict[str, object]:
        if query == "所以星宇呢":
            return {
                "ok": False,
                "status": "not_found",
                "candidates": [],
                "suggestions": [{"code": "2646", "name": "星宇航空"}],
            }
        if query == "2646":
            return {"ok": True, "stock": {"code": "2646", "name": "星宇航空"}}
        if query == "2610":
            return {"ok": True, "stock": {"code": "2610", "name": "華航"}}
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    first = line_bot_service._answer_stock_question_result(
        "所以星宇呢",
        conversation_context={"code": "2610", "stock_name": "華航", "last_focus": "overview"},
    )

    assert first.text == "你是指星宇航空（2646）嗎？如果是，回覆「是」就好，我接著幫你分析；如果不是，再告訴我完整名稱。"
    assert first.context_update["code"] == "2610"
    assert first.context_update["pending_stock_code"] == "2646"

    monkeypatch.setattr(line_bot_service, "fetch_daily_market_data", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(line_bot_service, "_compact_daily", lambda _payload: _focused_daily_facts())
    monkeypatch.setattr(line_bot_service, "_fallback_daily", lambda _facts: "星宇航空（2646）分析結果")
    monkeypatch.setenv("QWEN_ENABLED", "false")
    second = line_bot_service._answer_stock_question_result(
        "是",
        conversation_context=first.context_update,
    )

    assert second.text == "星宇航空（2646）分析結果"
    assert second.context_update["code"] == "2646"


def test_conversation_context_is_bounded_to_each_hashed_chat_user(monkeypatch) -> None:
    monkeypatch.setenv("LINE_CONVERSATION_TTL_SECONDS", "1800")
    monkeypatch.setenv("LINE_CONVERSATION_MAX_SESSIONS", "10")
    first = {"source": {"type": "user", "userId": "U-ONE"}}
    second = {"source": {"type": "user", "userId": "U-TWO"}}
    first_key = line_bot_service._conversation_key(first)
    second_key = line_bot_service._conversation_key(second)
    line_bot_service._clear_conversation_context(first_key)
    line_bot_service._clear_conversation_context(second_key)

    line_bot_service._commit_conversation_context(
        first_key,
        {"code": "2330", "stock_name": "台積電", "last_focus": "overview", "trade_date": "2026-08-21"},
    )

    assert first_key != second_key
    assert line_bot_service._conversation_snapshot(first_key)["code"] == "2330"
    assert line_bot_service._conversation_snapshot(second_key) == {}
    assert "U-ONE" not in first_key
    line_bot_service._clear_conversation_context(first_key)


def test_focused_policy_accepts_requested_claim_and_rejects_full_report_repetition() -> None:
    facts = line_bot_service._facts_for_focus(
        _focused_daily_facts(),
        "valuation",
        continued=True,
        previous_focus="overview",
    )
    assert facts["evidence_summary"]["limits"] == [
        "缺少同業或歷史估值基準，只能列示估值數值"
    ]
    claim = "估值 PE／PB／殖利率%：25.5／7.1／1.8"
    evidence = "缺少同業或歷史估值基準，只能列示估值數值"
    focused = f"{claim}\n{evidence}，不能判定高估或低估。\n僅供資料整理，不構成投資建議。"
    repeated = (
        f"資料品質\n綜合觀察\n判斷邏輯\n風險與待確認\n{claim}\n{evidence}\n"
        "僅供資料整理，不構成投資建議。"
    )

    assert line_bot_service._answer_respects_financial_policy(
        focused,
        facts,
        "那本益比呢",
        require_daily_claims=True,
    )
    assert not line_bot_service._answer_respects_financial_policy(
        repeated,
        facts,
        "那本益比呢",
        require_daily_claims=True,
    )


def test_detail_follow_up_adds_relationships_instead_of_repeating_claims_only() -> None:
    facts = line_bot_service._facts_for_focus(
        _focused_daily_facts(),
        "support_resistance",
        continued=True,
        previous_focus="support_resistance",
        detail_requested=True,
    )
    answer = line_bot_service._fallback_focused_daily(facts, "support_resistance")

    assert "最新官方收盤：2410" in answer
    assert "視為區間而不是單一價位" in answer
    assert "量能是否同步" in answer


def test_rationale_focus_does_not_expose_unasked_zones_or_valuation_limits() -> None:
    facts = line_bot_service._facts_for_focus(
        _focused_daily_facts(),
        "rationale",
        continued=True,
        previous_focus="overview",
        detail_requested=True,
    )

    assert "support_zone" not in facts["referee"]
    assert "resistance_zone" not in facts["referee"]
    assert "limits" not in facts["evidence_summary"]


def test_rationale_policy_limits_evidence_and_requires_conversation_bridge() -> None:
    facts = line_bot_service._facts_for_focus(
        _focused_daily_facts(),
        "rationale",
        continued=True,
        previous_focus="overview",
    )
    claim = facts["verified_claims"][0]
    one_evidence = facts["answer_contract"]["required_evidence"][0]
    second_evidence = facts["answer_contract"]["required_evidence"][1]
    natural = (
        f"簡單說，我主要把趨勢、動能、量價和風險條件放在一起看。{claim}；{one_evidence}。"
        "如果你想，我可以接著只講 RSI、量價或支撐其中一項。"
        "僅供資料整理，不構成投資建議。"
    )
    too_much = (
        f"{claim}；{one_evidence}；{second_evidence}。"
        "如果你想，我可以接著說明。僅供資料整理，不構成投資建議。"
    )
    no_bridge = f"{claim}；{one_evidence}。僅供資料整理，不構成投資建議。"

    assert line_bot_service._answer_respects_financial_policy(
        natural,
        facts,
        "你的評估邏輯是什麼？",
        require_daily_claims=True,
    )
    assert not line_bot_service._answer_respects_financial_policy(
        too_much,
        facts,
        "你的評估邏輯是什麼？",
        require_daily_claims=True,
    )
    assert not line_bot_service._answer_respects_financial_policy(
        no_bridge,
        facts,
        "你的評估邏輯是什麼？",
        require_daily_claims=True,
    )


def test_follow_up_classifier_distinguishes_topic_from_unknown_company() -> None:
    assert line_bot_service._is_contextual_follow_up("那本益比呢")
    assert line_bot_service._is_contextual_follow_up("為什麼是警戒")
    assert line_bot_service._is_contextual_follow_up("那2026/08/20呢")
    assert line_bot_service._is_contextual_follow_up("基本面呢")
    assert line_bot_service._is_contextual_follow_up("籌碼面怎麼看")
    assert line_bot_service._is_contextual_follow_up("法人籌碼和成本呢")
    assert line_bot_service._is_contextual_follow_up("夜盤怎麼看")
    assert line_bot_service._is_contextual_follow_up("最近有什麼消息")
    assert line_bot_service._is_contextual_follow_up("他收盤價多少")
    assert line_bot_service._is_contextual_follow_up("他可以買嗎")
    assert line_bot_service._is_contextual_follow_up("建議購買它嗎？")
    assert line_bot_service._is_contextual_follow_up("該股可以續抱嗎")
    assert line_bot_service._is_contextual_follow_up("你的判斷方式是什麼？為什麼這樣建議")
    assert line_bot_service._is_contextual_follow_up("你的評估邏輯是什麼？")
    assert line_bot_service._is_contextual_follow_up("你評估時主要看什麼？")
    assert line_bot_service._is_contextual_follow_up("剛才是根據哪些面向判斷的？")
    assert line_bot_service._is_contextual_follow_up("這個結論考量了什麼？")
    assert line_bot_service._is_contextual_follow_up("你是怎麼判斷的？")
    assert line_bot_service._is_contextual_follow_up("你是怎麼分析的？")
    assert line_bot_service._is_contextual_follow_up("可以說明一下你剛才怎麼分析嗎？")
    assert line_bot_service._is_contextual_follow_up("這是如何得出來的？")
    assert line_bot_service._is_contextual_follow_up("這個結論怎麼來的？")
    assert line_bot_service._is_contextual_follow_up("你建議購買他嗎？現在這個價格購買是否有風險？")
    assert line_bot_service._is_contextual_follow_up("照你剛才的分析，現在買風險高嗎？")
    assert line_bot_service._is_contextual_follow_up("這個價格能買嗎？")
    assert line_bot_service._is_contextual_follow_up("依你前面的判斷，支撐在哪？")
    assert line_bot_service._is_contextual_follow_up("還可以問什麼")
    assert not line_bot_service._is_contextual_follow_up("不存在公司本益比")
    assert not line_bot_service._is_contextual_follow_up("是方可以買嗎")
    assert not line_bot_service._is_contextual_follow_up("昇達科現在可以買嗎")
    assert line_bot_service._response_focus(
        "再詳細一點",
        previous_focus="valuation",
        continued=True,
    ) == "valuation"
    assert line_bot_service._response_focus("他收盤價多少", continued=True) == "price"
    assert line_bot_service._response_focus("他可以買嗎", continued=True) == "decision"
    assert line_bot_service._response_focus("建議購買它嗎？", continued=True) == "decision"
    assert line_bot_service._response_focus(
        "你的判斷方式是什麼？為什麼這樣建議",
        previous_focus="decision",
        continued=True,
    ) == "rationale"
    assert line_bot_service._response_focus(
        "你是怎麼分析的？",
        previous_focus="overview",
        continued=True,
    ) == "rationale"
    assert line_bot_service._response_focus(
        "你的評估邏輯是什麼？",
        previous_focus="overview",
        continued=True,
    ) == "rationale"
    assert line_bot_service._response_focus(
        "RSI 跟 MACD 技術面，他明天可以買嗎？",
        continued=True,
    ) == "technical_decision"


def test_screenshot_rationale_follow_up_reuses_china_airlines_context(monkeypatch) -> None:
    resolve_calls: list[str] = []
    replies: list[str] = []

    def fake_resolve(query: str) -> dict[str, object]:
        resolve_calls.append(query)
        if query in {"華航今天走勢怎麼樣幫我分析一下", "2610"}:
            return {"ok": True, "stock": {"code": "2610", "name": "華航"}}
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    facts = _focused_daily_facts()
    facts["code"] = "2610"
    facts["stock"] = {"name": "華航", "market": "listed", "exchange": "TWSE"}
    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    monkeypatch.setattr(line_bot_service, "fetch_daily_market_data", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(line_bot_service, "_compact_daily", lambda _payload: facts)
    monkeypatch.setattr(line_bot_service, "reply_text", lambda _token, text: replies.append(text))
    monkeypatch.setenv("QWEN_ENABLED", "false")

    source = {"type": "user", "userId": "U-CHINA-AIRLINES-RATIONALE"}
    context_key = line_bot_service._conversation_key({"source": source})
    line_bot_service._clear_conversation_context(context_key)
    try:
        for index, question in enumerate(
            ("華航今天走勢怎麼樣幫我分析一下", "你是怎麼分析的？"),
            start=1,
        ):
            line_bot_service.handle_line_event(
                {
                    "webhookEventId": f"china-airlines-rationale-{index}",
                    "type": "message",
                    "replyToken": f"reply-china-airlines-{index}",
                    "source": source,
                    "message": {"type": "text", "text": question},
                }
            )

        assert resolve_calls == ["華航今天走勢怎麼樣幫我分析一下", "2610"]
        assert len(replies) == 2
        assert "簡單說，我評估華航（2610）時" in replies[1]
        assert "趨勢位置、動能變化、量價是否配合，以及支撐失效風險" in replies[1]
        assert "目前判斷：中性" in replies[1]
        assert "無法確認你指的是哪一檔股票" not in replies[1]
        assert "裁判層" not in replies[1]
    finally:
        line_bot_service._clear_conversation_context(context_key)


def test_screenshot_evaluation_logic_follow_up_reuses_starlux_and_stays_high_level(monkeypatch) -> None:
    resolve_calls: list[str] = []

    def fake_resolve(query: str) -> dict[str, object]:
        resolve_calls.append(query)
        if query == "2646":
            return {"ok": True, "stock": {"code": "2646", "name": "星宇航空"}}
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    facts = _focused_daily_facts()
    facts["code"] = "2646"
    facts["stock"] = {"name": "星宇航空", "market": "listed", "exchange": "TWSE"}
    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    monkeypatch.setattr(line_bot_service, "fetch_daily_market_data", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(line_bot_service, "_compact_daily", lambda _payload: facts)
    monkeypatch.setenv("QWEN_ENABLED", "false")

    result = line_bot_service._answer_stock_question_result(
        "你的評估邏輯是什麼？",
        conversation_context={
            "code": "2646",
            "stock_name": "星宇航空",
            "last_focus": "overview",
            "last_mode": "stock",
            "trade_date": "2026-08-26",
            "recent_user_questions": ["星宇", "是"],
        },
    )

    assert resolve_calls == ["2646"]
    assert result.context_update["code"] == "2646"
    assert result.context_update["last_focus"] == "rationale"
    assert "簡單說，我評估星宇航空（2646）時" in result.text
    assert "趨勢位置、動能變化、量價是否配合，以及支撐失效風險" in result.text
    assert "目前判斷：中性" in result.text
    assert "你想的話，我可以接著只講 RSI、量價或支撐其中一項" in result.text
    assert "無法確認你指的是哪一檔股票" not in result.text
    assert not any(term in result.text for term in ("裁判層", "規則樹", "內部權重", "計算公式", "門檻值"))
    assert result.text.endswith("僅供資料整理，不構成投資建議。")


def test_general_investment_question_uses_natural_ai_without_stock_lookup_failure(monkeypatch) -> None:
    model_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        line_bot_service,
        "resolve_stock_query",
        lambda _query: {"ok": False, "status": "not_found", "candidates": [], "suggestions": []},
    )

    def fake_qwen(system_prompt: str, user_prompt: str, **_kwargs) -> str:
        model_calls.append((system_prompt, user_prompt))
        return (
            "RSI 可以幫你觀察價格動能，但不能單獨當成買賣訊號；"
            "最好再搭配趨勢、量能和支撐位置一起看。\n"
            "僅供資料整理，不構成投資建議。"
        )

    monkeypatch.setattr(line_bot_service, "qwen_chat", fake_qwen)
    monkeypatch.setenv("QWEN_ENABLED", "true")

    result = line_bot_service._answer_stock_question_result("RSI是什麼？")

    assert model_calls
    assert "自然對話" in model_calls[0][0]
    assert "RSI 可以幫你觀察價格動能" in result.text
    assert "股票名稱或四碼" not in result.text
    assert result.context_update is not None
    assert result.context_update["last_mode"] == "general"


def test_general_investment_question_has_conversational_safe_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        line_bot_service,
        "resolve_stock_query",
        lambda _query: {"ok": False, "status": "not_found", "candidates": [], "suggestions": []},
    )
    monkeypatch.setenv("QWEN_ENABLED", "false")

    answer = line_bot_service.answer_stock_question("股票投資要注意什麼？")

    assert "價格趨勢、動能、成交量、基本面、籌碼與最新事件" in answer
    assert "僅供資料整理，不構成投資建議。" in answer


def test_general_investment_policy_allows_indicator_ranges_but_rejects_fake_live_price() -> None:
    educational = (
        "RSI 介於 0 到 100，常見的 70 與 30 只適合當成觀察區，不是保證反轉。"
        "僅供資料整理，不構成投資建議。"
    )
    fake_live_price = (
        "今天現價 125 元，現在立刻買。"
        "僅供資料整理，不構成投資建議。"
    )

    assert line_bot_service._general_answer_respects_policy(educational, "RSI是什麼？")
    assert not line_bot_service._general_answer_respects_policy(fake_live_price, "RSI是什麼？")


def test_generic_stock_screening_question_does_not_require_stock_name(monkeypatch) -> None:
    screen_calls: list[tuple[str, int]] = []

    def fake_screen(*, strategy: str, limit: int) -> dict[str, object]:
        screen_calls.append((strategy, limit))
        return {
            "ok": True,
            "status": "ok",
            "trade_date": "2026-08-24",
            "strategy": strategy,
            "candidates": [
                {
                    "code": "2610",
                    "name": "華航",
                    "close": 20.5,
                    "action_state": "可小比例分批觀察",
                    "support": "20.1～20.3",
                    "resistance": "21～21.2",
                }
            ],
        }

    monkeypatch.setattr(line_bot_service, "fetch_stock_screen", fake_screen)
    monkeypatch.setattr(
        line_bot_service,
        "resolve_stock_query",
        lambda _query: (_ for _ in ()).throw(AssertionError("screening must not resolve one stock")),
    )

    result = line_bot_service._answer_stock_question_result("有哪些接近支撐、可以分批買的股票？")

    assert screen_calls == [("support", 5)]
    assert "華航（2610）" in result.text
    assert "可小比例分批觀察" in result.text
    assert "請輸入公司名稱" not in result.text


def test_what_stock_is_worth_buying_routes_to_bottom_screen_with_evidence(monkeypatch) -> None:
    screen_calls: list[tuple[str, int]] = []

    def fake_screen(*, strategy: str, limit: int) -> dict[str, object]:
        screen_calls.append((strategy, limit))
        return {
            "ok": True,
            "status": "ok",
            "trade_date": "2026-08-25",
            "strategy": strategy,
            "candidates": [
                {
                    "code": "3481",
                    "name": "群創",
                    "close": 46.8,
                    "rsi14": 43.7,
                    "action_state": "低檔止跌，可條件式第一批",
                    "low_zone_stage_label": "初步止跌，可條件式第一批",
                    "low_zone_summary": "RSI14 43.7 較前一日回升；支撐守住、MACD 與價格同步止穩，報酬風險比約 1.79。",
                    "buy_plan": "第一批只在支撐續守時小比例評估；第二批等待 RSI 與 MACD 續改善。",
                    "invalidation": "收盤跌破支撐下緣 44.45 即取消後續批次。",
                    "support": "44.45～46.5",
                    "resistance": "47.45～51",
                }
            ],
        }

    monkeypatch.setattr(line_bot_service, "fetch_stock_screen", fake_screen)
    monkeypatch.setattr(
        line_bot_service,
        "resolve_stock_query",
        lambda _query: (_ for _ in ()).throw(
            AssertionError("generic stock question must not reuse the prior stock")
        ),
    )

    result = line_bot_service._answer_stock_question_result(
        "那什麼股票值得買",
        conversation_context={"code": "2303", "stock_name": "聯電", "last_focus": "decision"},
    )

    assert screen_calls == [("bottom", 5)]
    assert "群創（3481）" in result.text
    assert "RSI14 43.7" in result.text
    assert "支撐守住" in result.text
    assert "分批條件" in result.text
    assert "失效" in result.text
    assert "目前資料僅涵蓋聯電" not in result.text


def test_single_stock_advice_is_not_misclassified_as_market_screen(monkeypatch) -> None:
    assert not line_bot_service._is_stock_screening_question("華航可以買嗎？")
    assert not line_bot_service._is_stock_screening_question("建議購買它嗎？")
    assert line_bot_service._is_stock_screening_question("幫我選股，找幾檔穩健的股票")
    assert line_bot_service._is_stock_screening_question("今天有什麼可以買？")
    assert line_bot_service._is_stock_screening_question("推薦可以買的")
    assert line_bot_service._screening_strategy("幫我找強勢突破股票") == "momentum"
    assert line_bot_service._screening_strategy("有什麼保守低風險標的") == "conservative"
    assert line_bot_service._screening_strategy("那什麼股票值得買") == "bottom"
    assert line_bot_service._screening_strategy("找 RSI 較低且已止跌的股票") == "bottom"


def test_advice_pronoun_follow_up_reuses_china_airlines_context(monkeypatch) -> None:
    resolve_calls: list[str] = []

    def fake_resolve(query: str) -> dict[str, object]:
        resolve_calls.append(query)
        if query == "2610":
            return {"ok": True, "stock": {"code": "2610", "name": "華航"}}
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    monkeypatch.setattr(line_bot_service, "fetch_daily_market_data", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(line_bot_service, "_compact_daily", lambda _payload: _focused_daily_facts())
    monkeypatch.setenv("QWEN_ENABLED", "false")

    result = line_bot_service._answer_stock_question_result(
        "建議購買它嗎？",
        conversation_context={
            "code": "2610",
            "stock_name": "華航",
            "last_focus": "price",
            "recent_user_questions": ["華航今天的收盤價是多少？"],
        },
    )

    assert resolve_calls == ["2610"]
    assert result.context_update is not None
    assert result.context_update["code"] == "2610"
    assert result.context_update["last_focus"] == "decision"
    assert "進出場判斷" in result.text
    assert "找不到對應" not in result.text


def test_pronoun_follow_up_reuses_same_line_user_context(monkeypatch) -> None:
    resolve_calls: list[str] = []
    replies: list[str] = []

    def fake_resolve(query: str) -> dict[str, object]:
        resolve_calls.append(query)
        if query in {"台積電股價多少", "2330"}:
            return {"ok": True, "stock": {"code": "2330", "name": "台積電"}}
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    facts = _focused_daily_facts()
    facts["advisory"] = {
        "decision_ready": True,
        "headline": "現在不適合追價。",
        "buy_plan": "若未持有，先等支撐守穩後再分批評估。",
        "holder_plan": "若已持有，跌破支撐時降低曝險。",
        "invalidation": "收盤跌破支撐下緣時失效。",
        "background_notes": [],
        "personalization_question": "你是未持有還是已有部位？",
    }
    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    monkeypatch.setattr(line_bot_service, "fetch_daily_market_data", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(line_bot_service, "_compact_daily", lambda _payload: facts)
    monkeypatch.setattr(line_bot_service, "reply_text", lambda _token, text: replies.append(text))
    monkeypatch.setenv("QWEN_ENABLED", "false")

    source = {"type": "user", "userId": "U-PRONOUN-CONTEXT"}
    context_key = line_bot_service._conversation_key({"source": source})
    line_bot_service._clear_conversation_context(context_key)
    try:
        line_bot_service.handle_line_event(
            {
                "webhookEventId": "pronoun-context-first",
                "type": "message",
                "replyToken": "reply-first",
                "source": source,
                "message": {"type": "text", "text": "台積電股價多少"},
            }
        )
        line_bot_service.handle_line_event(
            {
                "webhookEventId": "pronoun-context-second",
                "type": "message",
                "replyToken": "reply-second",
                "source": source,
                "message": {"type": "text", "text": "他可以買嗎"},
            }
        )

        assert resolve_calls == ["台積電股價多少", "2330"]
        assert len(replies) == 2
        assert "台積電（2330）這題我接著說明進出場判斷" in replies[1]
        assert "找不到對應" not in replies[1]
        assert line_bot_service._conversation_snapshot(context_key)["code"] == "2330"
    finally:
        line_bot_service._clear_conversation_context(context_key)


def test_natural_follow_ups_reuse_previous_stock_across_line_turns(monkeypatch) -> None:
    resolve_calls: list[str] = []
    replies: list[str] = []

    def fake_resolve(query: str) -> dict[str, object]:
        resolve_calls.append(query)
        if query in {"國巨今天收盤多少", "2327"}:
            return {"ok": True, "stock": {"code": "2327", "name": "國巨"}}
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    facts = _focused_daily_facts()
    facts["code"] = "2327"
    facts["stock"] = {"name": "國巨", "market": "listed", "exchange": "TWSE"}
    facts["advisory"] = {
        "decision_ready": True,
        "headline": "目前不適合追價。",
        "buy_plan": "若未持有，等待支撐守穩後再分批評估。",
        "holder_plan": "若已持有，跌破支撐時降低曝險。",
        "invalidation": "收盤跌破支撐下緣時失效。",
        "background_notes": [],
        "personalization_question": "你是未持有還是已有部位？",
    }
    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    monkeypatch.setattr(line_bot_service, "fetch_daily_market_data", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(line_bot_service, "_compact_daily", lambda _payload: facts)
    monkeypatch.setattr(line_bot_service, "reply_text", lambda _token, text: replies.append(text))
    monkeypatch.setenv("QWEN_ENABLED", "false")

    source = {"type": "user", "userId": "U-RATIONALE-CONTEXT"}
    context_key = line_bot_service._conversation_key({"source": source})
    line_bot_service._clear_conversation_context(context_key)
    try:
        questions = (
            "國巨今天收盤多少",
            "可以買嗎",
            "你的判斷方式是什麼？為什麼這樣建議",
            "你建議購買他嗎？現在這個價格購買是否有風險？",
        )
        for index, question in enumerate(questions, start=1):
            line_bot_service.handle_line_event(
                {
                    "webhookEventId": f"rationale-context-{index}",
                    "type": "message",
                    "replyToken": f"reply-rationale-{index}",
                    "source": source,
                    "message": {"type": "text", "text": question},
                }
            )

        assert resolve_calls == ["國巨今天收盤多少", "2327", "2327", "2327"]
        assert len(replies) == 4
        assert "簡單說，我評估國巨（2327）時" in replies[2]
        assert "你想的話，我可以接著只講 RSI、量價或支撐其中一項" in replies[2]
        assert "找不到對應" not in replies[2]
        assert "國巨（2327）這題我接著說明進出場判斷" in replies[3]
        assert "找不到對應" not in replies[3]
        snapshot = line_bot_service._conversation_snapshot(context_key)
        assert snapshot["code"] == "2327"
        assert snapshot["last_focus"] == "decision"
    finally:
        line_bot_service._clear_conversation_context(context_key)


def test_compound_buy_risk_question_reuses_previous_stock(monkeypatch) -> None:
    resolve_calls: list[str] = []

    def fake_resolve(query: str) -> dict[str, object]:
        resolve_calls.append(query)
        if query == "3491":
            return {"ok": True, "stock": {"code": "3491", "name": "昇達科"}}
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    resolution, continued = line_bot_service._resolve_question_with_context(
        "你建議購買他嗎？現在這個價格購買是否有風險？",
        {"code": "3491", "stock_name": "昇達科", "last_focus": "price"},
    )

    assert continued is True
    assert resolution["stock"]["code"] == "3491"
    assert resolve_calls == ["3491"]
    assert line_bot_service._response_focus(
        "你建議購買他嗎？現在這個價格購買是否有風險？",
        previous_focus="price",
        continued=True,
    ) == "decision"


def test_chart_follow_up_phrases_reuse_stock_and_route_to_technical(monkeypatch) -> None:
    calls: list[str] = []

    def fake_resolve(query: str) -> dict[str, object]:
        calls.append(query)
        return {"ok": True, "stock": {"code": "2330", "name": "台積電"}}

    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    context = {
        "code": "2330",
        "stock_name": "台積電",
        "last_focus": "chart",
        "last_chart_analysis": {"status": "ok", "indicators": [{"name": "RSI"}]},
    }
    resolution, continued = line_bot_service._resolve_question_with_context(
        "這張圖的 RSI 呢？",
        context,
    )

    assert continued is True
    assert resolution["stock"]["code"] == "2330"
    assert calls == ["2330"]
    assert line_bot_service._response_focus(
        "這張圖怎麼看？",
        previous_focus="chart",
        continued=True,
    ) == "rationale"
    assert line_bot_service._response_focus(
        "詳細一點",
        previous_focus="chart",
        continued=True,
    ) == "technical"


def test_image_event_downloads_analyzes_replies_and_commits_chart_context(monkeypatch) -> None:
    replies: list[str] = []
    captured: dict[str, object] = {}
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(
        line_bot_service,
        "get_line_image_content",
        lambda message_id, **kwargs: (
            captured.update({"message_id": message_id, **kwargs})
            or line_messaging.LineImageContent(b"image-bytes", "image/png")
        ),
    )
    context_update = {
        "code": "2330",
        "stock_name": "台積電",
        "last_focus": "chart",
        "last_mode": "image",
        "trade_date": "2026-08-26",
        "last_chart_analysis": {
            "status": "ok",
            "indicators": [{"name": "RSI", "value": 42.6}],
            "can_override_main_status": False,
        },
    }
    monkeypatch.setattr(
        line_bot_service,
        "_answer_chart_image_result",
        lambda image_bytes, **kwargs: (
            captured.update({"image_bytes": image_bytes, **kwargs})
            or line_bot_service._AnswerResult(
                "我看得到這張 K 線圖。\n僅供資料整理，不構成投資建議。",
                context_update=context_update,
            )
        ),
    )
    monkeypatch.setattr(line_bot_service, "reply_text", lambda token, text: replies.append(text))
    event = {
        "webhookEventId": "image-event-success",
        "type": "message",
        "replyToken": "reply-image",
        "source": {"type": "user", "userId": "U-IMAGE-CONTEXT"},
        "message": {
            "id": "987654321",
            "type": "image",
            "contentProvider": {"type": "line"},
        },
    }
    context_key = line_bot_service._conversation_key(event)
    line_bot_service._clear_conversation_context(context_key)
    try:
        line_bot_service.handle_line_event(event)
        snapshot = line_bot_service._conversation_snapshot(context_key)
        assert captured["message_id"] == "987654321"
        assert captured["image_bytes"] == b"image-bytes"
        assert replies and "K 線圖" in replies[0]
        assert snapshot["code"] == "2330"
        assert snapshot["last_focus"] == "chart"
        assert snapshot["last_chart_analysis"]["can_override_main_status"] is False
    finally:
        line_bot_service._clear_conversation_context(context_key)


def test_chart_reply_is_natural_but_keeps_image_values_estimated(monkeypatch) -> None:
    monkeypatch.setattr(
        line_bot_service,
        "_compact_daily",
        lambda _payload: {
            "trade_date": "2026-08-26",
            "technical": {
                "decision_ready": True,
                "rsi": {"rsi14": 41.9},
                "macd": {"oscillator": -0.8},
                "moving_averages": {"ma5": 940.0, "ma20": 952.0, "ma60": 910.0},
            },
            "referee": {"decision_ready": True, "main_status": "中性觀察"},
        },
    )
    answer = line_bot_service._format_chart_image_result(
        {
            "ok": True,
            "status": "ok",
            "stock": {"code": "2330", "name": "台積電"},
            "quality": {
                "timeframe": "日K",
                "chart_type": "K線圖",
                "indicators": [
                    {"name": "RSI", "period": "14", "value": 42.6, "signal": "回升"}
                ],
                "chart_observations": ["近期低點略為墊高"],
                "uncertainty_reasons": [],
            },
            "official_payload": {"status": "ok"},
            "can_override_main_status": False,
        }
    )

    assert "我看得懂" in answer
    assert "圖上可辨識的推估讀值：RSI14 42.6" in answer
    assert "最近完整交易日 2026-08-26" in answer
    assert "目前判斷仍是「中性觀察」" in answer
    assert "你想接著看 RSI" in answer
    assert "FACTS" not in answer
    assert "referee" not in answer


def test_text_turn_preserves_previous_chart_summary() -> None:
    key = "user:preserve-chart-summary"
    line_bot_service._clear_conversation_context(key)
    try:
        line_bot_service._commit_conversation_context(
            key,
            {
                "code": "2330",
                "stock_name": "台積電",
                "last_focus": "chart",
                "last_chart_analysis": {"status": "ok", "timeframe": "日K"},
            },
        )
        line_bot_service._commit_conversation_context(
            key,
            {"code": "2330", "stock_name": "台積電", "last_focus": "rsi"},
        )
        snapshot = line_bot_service._conversation_snapshot(key)
        assert snapshot["last_focus"] == "rsi"
        assert snapshot["last_chart_analysis"]["timeframe"] == "日K"
    finally:
        line_bot_service._clear_conversation_context(key)


def test_generic_follow_up_uses_context_before_stock_name_substring(monkeypatch) -> None:
    calls: list[str] = []

    def fake_resolve(query: str) -> dict[str, object]:
        calls.append(query)
        if query == "2330":
            return {"ok": True, "stock": {"code": "2330", "name": "台積電"}}
        if "和成" in query:
            return {"ok": True, "stock": {"code": "1810", "name": "和成"}}
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    resolution, continued = line_bot_service._resolve_question_with_context(
        "法人籌碼和成本呢",
        {"code": "2330", "stock_name": "台積電", "last_focus": "overview"},
    )

    assert continued is True
    assert resolution["stock"]["code"] == "2330"
    assert calls == ["2330"]


def test_discourse_marker_can_switch_to_an_explicit_new_stock(monkeypatch) -> None:
    calls: list[str] = []

    def fake_resolve(query: str) -> dict[str, object]:
        calls.append(query)
        if query == "那華航呢":
            return {
                "ok": True,
                "status": "ok",
                "stock": {"code": "2610", "name": "華航", "official_name": "中華航空股份有限公司"},
            }
        if query == "2330":
            return {"ok": True, "stock": {"code": "2330", "name": "台積電"}}
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    resolution, continued = line_bot_service._resolve_question_with_context(
        "那華航呢",
        {"code": "2330", "stock_name": "台積電", "last_focus": "overview"},
    )

    assert continued is False
    assert resolution["stock"]["code"] == "2610"
    assert calls == ["那華航呢"]


def test_buy_question_uses_decision_focus_and_hides_unasked_indicators() -> None:
    facts = _focused_daily_facts()
    facts["advisory"] = {
        "decision_ready": True,
        "headline": "位置接近支撐，可考慮條件式分批，但不適合一次押滿。",
        "buy_plan": "若未持有，可把 2340～2405 視為第一批觀察區；前提是收盤沒有跌破支撐下緣。",
        "holder_plan": "若已持有，可續抱觀察，但跌破支撐下緣時應降低部位。",
        "invalidation": "這套判斷在收盤跌破支撐下緣 2340 時失效。",
        "background_notes": [],
        "personalization_question": "你是未持有還是已有部位？",
    }
    facts["institutional_context"] = {"available": False}
    facts["global_market_context"] = {"available": False}

    projected = line_bot_service._facts_for_focus(
        facts,
        "decision",
        continued=True,
        previous_focus="overview",
        decision_intent="buy",
        user_profile={},
        recent_user_questions=["分析 2330", "那能不能買"],
    )
    answer = line_bot_service._fallback_focused_daily(projected, "decision")

    assert line_bot_service._response_focus("那現在能不能買", continued=True) == "decision"
    assert "條件式分批" in answer
    assert "2340～2405" in answer
    assert "RSI5／RSI10／RSI14" not in answer
    assert "MACD DIF／Signal／OSC" not in answer
    assert "估值 PE／PB" not in answer
    assert "technical" not in projected
    assert "valuation" not in projected


def test_technical_buy_question_answers_requested_indicators_before_trade_plan() -> None:
    facts = _focused_daily_facts()
    facts["advisory"] = {
        "decision_ready": True,
        "headline": "位置接近支撐，可考慮條件式分批，但不適合一次押滿。",
        "buy_plan": "若未持有，可把 2340～2405 視為第一批觀察區；前提是收盤沒有跌破支撐下緣。",
        "holder_plan": "若已持有，可續抱觀察，但跌破支撐下緣時應降低部位。",
        "invalidation": "這套判斷在收盤跌破支撐下緣 2340 時失效。",
        "background_notes": [],
    }
    question = "RSI 跟 MACD 技術面，他明天可以買嗎？"
    topics = line_bot_service._requested_technical_topics(question)
    projected = line_bot_service._facts_for_focus(
        facts,
        "technical_decision",
        continued=True,
        previous_focus="decision",
        decision_intent="buy",
        user_profile={},
        technical_topics=topics,
    )
    answer = line_bot_service._fallback_focused_daily(projected, "technical_decision")

    assert topics == {"rsi", "macd"}
    assert "直接回答" in answer
    assert "RSI5／RSI10／RSI14：60.55／54.81／53.37" in answer
    assert "MACD DIF／Signal／OSC：5.7165／3.5161／2.2005" in answer
    assert "RSI14 與 MACD 同步偏多" in answer
    assert "條件式分批" in answer
    assert "2340～2405" in answer
    assert "MA5／MA10／MA20／MA60" not in answer
    assert "估值 PE／PB" not in answer
    assert set(projected["technical"]) >= {"rsi", "macd"}
    assert "moving_averages" not in projected["technical"]
    assert set(projected["evidence_summary"]) == {
        "momentum_logic",
        "can_override_main_status",
    }
    assert projected["answer_contract"]["required_evidence"] == [
        "RSI14 與 MACD 同步偏多"
    ]
    assert answer.index("直接回答") < answer.index("RSI5／RSI10／RSI14")


def test_decision_policy_accepts_only_backend_approved_action_phrases() -> None:
    facts = _focused_daily_facts()
    facts["advisory"] = {
        "decision_ready": True,
        "headline": "現在不適合追價。",
        "buy_plan": "若未持有，先等 95～98 附近止穩，不要一次投入。",
        "holder_plan": "若已持有，跌破 95 時應降低曝險。",
        "invalidation": "收盤跌破 95 時失效。",
    }
    projected = line_bot_service._facts_for_focus(
        facts,
        "decision",
        continued=True,
        previous_focus="overview",
        decision_intent="buy",
        user_profile={},
    )
    approved = projected["answer_contract"]["approved_advisory_claims"]
    answer = "\n".join([*approved, "僅供資料整理，不構成投資建議。"])
    invented = answer + "\n建議立即買進。"

    assert line_bot_service._answer_respects_financial_policy(
        answer,
        projected,
        "能不能買",
        require_daily_claims=True,
    )
    assert not line_bot_service._answer_respects_financial_policy(
        invented,
        projected,
        "能不能買",
        require_daily_claims=True,
    )


def test_question_memory_is_bounded_and_profile_requires_explicit_words(monkeypatch) -> None:
    monkeypatch.setenv("LINE_CONVERSATION_MAX_TURNS", "3")
    monkeypatch.setenv("LINE_CONVERSATION_MAX_USER_CHARS", "400")
    history = line_bot_service._bounded_question_history(
        ["第一題", "第二題", "第三題"],
        "第四題",
    )
    profile = line_bot_service._profile_for_question(
        "我已持有，想做波段，現在該續抱嗎",
        {},
    )

    assert history == ["第二題", "第三題", "第四題"]
    assert profile == {"position_state": "holding", "investment_horizon": "swing"}


def _stub_unavailable_fundamental_follow_up(monkeypatch) -> None:
    def fake_resolve(query: str) -> dict[str, object]:
        if query == "2330":
            return {"ok": True, "stock": {"code": "2330", "name": "台積電"}}
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    monkeypatch.setattr(line_bot_service, "fetch_daily_market_data", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(line_bot_service, "_compact_daily", lambda _payload: _focused_daily_facts())
    monkeypatch.setattr(
        line_bot_service,
        "ensure_background_text_model_warmup",
        lambda: {"status": "resident", "scheduled": False},
    )
    monkeypatch.setenv("QWEN_ENABLED", "true")


def test_unavailable_fundamentals_allow_bounded_model_limitation_analysis(monkeypatch) -> None:
    _stub_unavailable_fundamental_follow_up(monkeypatch)
    calls: list[tuple[str, str, dict[str, object]]] = []
    model_answer = (
        "目前資料未完整帶入營收、EPS 與 ROE，所以只能說明判讀框架，不能據此形成基本面結論；"
        "待營收趨勢、獲利率、現金流與公司展望資料齊備後，才能比較是否同步改善。\n"
        "僅供資料整理，不構成投資建議。"
    )

    def fake_qwen(system_prompt: str, user_prompt: str, **kwargs: object) -> str:
        calls.append((system_prompt, user_prompt, dict(kwargs)))
        return model_answer

    monkeypatch.setattr(line_bot_service, "qwen_chat", fake_qwen)

    answer = line_bot_service.answer_stock_question(
        "基本面呢",
        conversation_context={"code": "2330", "stock_name": "台積電", "last_focus": "overview"},
    )


    assert answer == model_answer
    assert len(calls) == 1
    assert '"requested_focus":"fundamentals"' in calls[0][1]
    assert '"valuation"' not in calls[0][1]


def test_unavailable_fundamentals_reject_ungrounded_numeric_claims(monkeypatch) -> None:
    _stub_unavailable_fundamental_follow_up(monkeypatch)
    calls = 0

    def fake_qwen(*_args: object, **_kwargs: object) -> str:
        nonlocal calls
        calls += 1
        return "雖然資料缺少，但 EPS 是 88.88。\n僅供資料整理，不構成投資建議。"

    monkeypatch.setattr(line_bot_service, "qwen_chat", fake_qwen)

    answer = line_bot_service.answer_stock_question(
        "基本面呢",
        conversation_context={"code": "2330", "stock_name": "台積電", "last_focus": "overview"},
    )

    assert calls == 1
    assert "88.88" not in answer
    assert "還沒有完整帶入營收、EPS、ROE" in answer
    assert "不能假裝完成基本面分析" in answer


def test_unavailable_fundamentals_skip_model_when_deadline_admission_fails(monkeypatch) -> None:
    _stub_unavailable_fundamental_follow_up(monkeypatch)
    qwen = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Qwen must not run"))
    monkeypatch.setattr(line_bot_service, "qwen_chat", qwen)

    answer = line_bot_service.answer_stock_question(
        "基本面呢",
        deadline_monotonic=0.0,
        conversation_context={"code": "2330", "stock_name": "台積電", "last_focus": "overview"},
    )

    assert "還沒有完整帶入營收、EPS、ROE" in answer
    assert "不能假裝完成基本面分析" in answer


def test_stock_answer_uses_existing_db_fallback_when_predicted_deadline_is_rejected(monkeypatch) -> None:
    _stub_unavailable_fundamental_follow_up(monkeypatch)
    qwen_calls = 0

    def fake_qwen(*_args: object, **_kwargs: object) -> str:
        nonlocal qwen_calls
        qwen_calls += 1
        raise AssertionError("the rejected model callable must not execute")

    def reject_admission(*_args: object, **_kwargs: object):
        raise line_bot_service.ModelAdmissionError(
            "predicted miss",
            reason_code="predicted_deadline_admission_rejected",
        )

    monkeypatch.setattr(line_bot_service, "qwen_chat", fake_qwen)
    monkeypatch.setattr(line_bot_service, "run_interactive_model", reject_admission)

    answer = line_bot_service.answer_stock_question(
        "基本面呢",
        deadline_monotonic=time.monotonic() + 45,
        conversation_context={"code": "2330", "stock_name": "台積電", "last_focus": "overview"},
    )

    assert qwen_calls == 0
    assert "還沒有完整帶入營收、EPS、ROE" in answer
    assert "不能假裝完成基本面分析" in answer
    assert answer.endswith("僅供資料整理，不構成投資建議。")


def test_stable_missing_fundamentals_uses_real_predicted_deadline_admission_and_honest_fallback(
    monkeypatch, caplog,
) -> None:
    """Stable path only: real projection, controller rejection, and fallback; no V2 wiring claim."""
    controller = model_admission_service._AdmissionController()
    monkeypatch.setattr(model_admission_service, "_CONTROLLER", controller)
    assert line_bot_service.run_interactive_model is model_admission_service.run_interactive_model
    monkeypatch.setenv("QWEN_ENABLED", "true")
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "off")
    monkeypatch.setenv("LINE_MODEL_RESEARCH_ROLLOUT", "off")
    monkeypatch.setenv("LINE_MODEL_INTERACTIVE_P95_MS", "60000")
    monkeypatch.setenv("LINE_REPLY_TIMEOUT_SECONDS", "3")
    monkeypatch.setattr(
        line_bot_service, "now_tpe",
        lambda: datetime(2026, 8, 28, 16, 0, tzinfo=ZoneInfo("Asia/Taipei")),
    )
    monkeypatch.setattr(line_bot_service, "recent_market_date_for_eod", lambda: "2026-08-28")
    monkeypatch.setattr(line_bot_service, "is_taiwan_trading_day", lambda day: day.weekday() < 5)
    calls = {"resolve": [], "fetch": 0, "warmup": 0, "model": 0, "external_io": 0}
    # Synthetic adapter result, not a mock of _compact_daily or any analysis function.
    raw_daily = {
        "status": "ok", "code": "2330",
        "stock": {"name": "台積電", "market": "listed", "exchange": "TWSE"},
        "trade_date": "2026-08-28", "data_date": "2026-08-28",
        "analysis_mode": "close_batch",
        "ohlcv": {"official_trusted": True, "close": 100, "open": 100, "high": 101, "low": 99},
        "freshness": {"ready": True, "status": "current"},
        "valuation": {"available": False, "status": "unavailable"},
        "technical": {"available": False, "decision_ready": False},
        "data_quality": {"decision_ready": False},
        "referee": {"decision_ready": True, "main_status": "中性", "main_reasons": ["合成測試資料"]},
    }

    def resolve(query):
        calls["resolve"].append(query)
        if query == "2330":
            return {"ok": True, "stock": {"code": "2330", "name": "台積電"}}
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    def fetch(*_args, **_kwargs):
        calls["fetch"] += 1
        return raw_daily

    def resident_status():
        calls["warmup"] += 1
        return {"status": "resident", "scheduled": False}

    def forbidden_model(*_args, **_kwargs):
        calls["model"] += 1
        raise AssertionError("predicted deadline rejection must occur before model execution")

    def forbidden_io(*_args, **_kwargs):
        calls["external_io"] += 1
        raise AssertionError("offline admission contract must not access network or database")

    monkeypatch.setattr(line_bot_service, "resolve_stock_query", resolve)
    monkeypatch.setattr(line_bot_service, "fetch_daily_market_data", fetch)
    monkeypatch.setattr(line_bot_service, "ensure_background_text_model_warmup", resident_status)
    monkeypatch.setattr(line_bot_service, "qwen_chat", forbidden_model)
    monkeypatch.setattr(socket.socket, "connect", forbidden_io)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_io)
    monkeypatch.setattr(socket, "create_connection", forbidden_io)
    monkeypatch.setattr(sqlite3, "connect", forbidden_io)
    caplog.set_level("INFO", logger=line_bot_service.LOGGER.name)
    before = controller.snapshot()
    # 30s leaves ~25s after the real 5s reply reserve: it must pass the <5s preguard,
    # then fail the real controller's 60s predicted-duration admission check.
    result = line_bot_service._answer_stock_question_result(
        "基本面呢",
        deadline_monotonic=time.monotonic() + 30,
        conversation_context={"code": "2330", "stock_name": "台積電", "last_focus": "overview"},
    )
    after = controller.snapshot()
    print("OFFLINE_ADMISSION_EVIDENCE " + json.dumps({
        "synthetic": True, "scope": "stable_interactive_not_v2",
        "raw_daily": raw_daily, "calls": calls, "controller_before": before,
        "controller_after": after, "answer_path": result.answer_path,
        "text": result.text, "context_update": result.context_update,
        "admission_log": caplog.text,
    }, ensure_ascii=True))
    assert "2330" in calls["resolve"]
    assert calls["fetch"] == calls["warmup"] == 1
    assert calls["model"] == calls["external_io"] == 0
    assert after["predicted_deadline_rejected"] == before["predicted_deadline_rejected"] + 1
    assert after["deadline_rejected"] == after["completed_tasks"] == after["queue_depth"] == 0
    assert after["active_category"] is None
    assert result.answer_path == "admission_fallback:predicted_deadline_admission_rejected"
    assert "model task is predicted to miss its reply deadline" in caplog.text
    assert result.context_update["code"] == "2330"
    assert result.context_update["last_focus"] == "fundamentals"
    assert "還沒有完整帶入營收、EPS、ROE" in result.text
    assert "不能假裝完成基本面分析" in result.text
    assert result.text.endswith("僅供資料整理，不構成投資建議。")


def test_capabilities_follow_up_returns_topic_menu_instead_of_full_report(monkeypatch) -> None:
    def fake_resolve(query: str) -> dict[str, object]:
        if query == "2330":
            return {"ok": True, "stock": {"code": "2330", "name": "台積電"}}
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    monkeypatch.setattr(line_bot_service, "fetch_daily_market_data", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(line_bot_service, "_compact_daily", lambda _payload: _focused_daily_facts())
    monkeypatch.setenv("QWEN_ENABLED", "true")

    answer = line_bot_service.answer_stock_question(
        "還可以問什麼",
        conversation_context={"code": "2330", "stock_name": "台積電", "last_focus": "overview"},
    )

    assert "RSI、MACD、均線趨勢、量能、支撐／賣壓" in answer
    assert "官方開／高／低／收" not in answer


@pytest.fixture
def offline_deadline_handler(monkeypatch):
    """Replace external I/O only; clocks are module-local, never global time patches."""
    state = {
        "now": time.monotonic(), "context": {}, "trace": [], "replies": [],
        "telemetry": [], "shadow": [], "external_io_calls": 0,
        "model_calls": 0, "fail_reply": False,
    }
    monkeypatch.setattr(line_bot_service, "time", SimpleNamespace(monotonic=lambda: state["now"]))
    monkeypatch.setenv("LINE_TOTAL_REPLY_BUDGET_SECONDS", "45")
    monkeypatch.setenv("LINE_REPLY_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "off")
    monkeypatch.setenv("LINE_MODEL_RESEARCH_ROLLOUT", "off")
    monkeypatch.setattr(line_bot_service, "configured_line_user_ids", lambda: [])
    memory = SimpleNamespace(
        begin_event=lambda _event: True,
        context_for_event=lambda _event: dict(state["context"]),
        finish_event=lambda _event, *, delivered: state["trace"].append(f"finish:{delivered}"),
        commit_after_delivery=lambda *_args, **_kwargs: state["trace"].append("memory_commit"),
    )
    monkeypatch.setattr(line_bot_service, "conversation_memory_service", lambda: memory)
    monkeypatch.setattr(line_bot_service, "_conversation_snapshot", lambda _key: {})
    monkeypatch.setattr(
        line_bot_service, "_commit_conversation_context",
        lambda *_args: state["trace"].append("context_commit"),
    )

    def reply(_token, text):
        state["trace"].append("reply")
        state["replies"].append(text)
        if state["fail_reply"]:
            raise line_bot_service.LineMessagingError("synthetic reply failure")

    def telemetry(record):
        state["trace"].append("telemetry")
        state["telemetry"].append(dict(record))

    def shadow(_job, *, stable_reply_sha256):
        state["trace"].append("shadow")
        state["shadow"].append({"stable_reply_sha256": stable_reply_sha256})

    def forbidden_io(*_args, **_kwargs):
        state["external_io_calls"] += 1
        raise AssertionError("deadline tests must not access network or real database")

    monkeypatch.setattr(line_bot_service, "reply_text", reply)
    monkeypatch.setattr(line_bot_service, "append_line_reply_telemetry", telemetry)
    monkeypatch.setattr(line_bot_service, "submit_line_model_shadow", shadow)
    # TestClient's Windows event loop needs an internal socketpair. Block the
    # actual adapters' HTTP transport, including reused connections, not that IPC.
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden_io)
    monkeypatch.setattr(socket, "create_connection", forbidden_io)
    monkeypatch.setattr(sqlite3, "connect", forbidden_io)
    yield state
    assert state["external_io_calls"] == 0


def _deadline_event(suffix="one"):
    return {
        "webhookEventId": "offline-deadline-" + suffix, "type": "message",
        "replyToken": "synthetic-reply-" + suffix,
        "source": {"type": "user", "userId": "synthetic-deadline-user"},
        "message": {"type": "text", "text": "基本面呢"},
    }


def _print_deadline_evidence(case_id, state, **extra):
    print("OFFLINE_DEADLINE_EVIDENCE " + json.dumps({
        "case_id": case_id, "synthetic": True, "trace": state["trace"],
        "reply_texts": state["replies"],
        "reply_utf8_hex": [text.encode("utf-8").hex() for text in state["replies"]],
        "answer_paths": [item["answer_path"] for item in state["telemetry"]],
        "model_calls": state["model_calls"], "external_io_calls": state["external_io_calls"],
        **extra,
    }, ensure_ascii=True))


@pytest.mark.parametrize("queue_delay", [0.0, 10.0, 40.0])
def test_handler_deadline_uses_webhook_ingress(monkeypatch, offline_deadline_handler, queue_delay):
    state = offline_deadline_handler
    state["now"] = 100.0 + queue_delay
    deadlines = []

    def capture_answer(_question, *, deadline_monotonic, conversation_context, cohort_key=""):
        deadlines.append(deadline_monotonic)
        assert cohort_key == "offline-deadline-one"
        return line_bot_service._AnswerResult("合成期限測試", answer_path="synthetic_capture")

    monkeypatch.setattr(line_bot_service, "_answer_stock_question_result", capture_answer)
    line_bot_service.handle_line_event(_deadline_event(), webhook_ingress_monotonic=100.0)
    _print_deadline_evidence(
        f"ingress-delay-{int(queue_delay)}", state, deadlines=deadlines,
        remaining_seconds=deadlines[0] - state["now"],
    )
    assert deadlines == [145.0]
    assert deadlines[0] - state["now"] == 45.0 - queue_delay
    assert state["telemetry"][0]["webhook_ingress_to_reply_ms"] == int(queue_delay * 1000)
    assert state["replies"] == ["合成期限測試"]


def test_handler_deadline_without_ingress_preserves_direct_call_compatibility(
    monkeypatch, offline_deadline_handler,
):
    state = offline_deadline_handler
    state["now"] = 120.0
    deadlines = []

    def capture_answer(_question, *, deadline_monotonic, conversation_context, cohort_key=""):
        deadlines.append(deadline_monotonic)
        assert cohort_key == "offline-deadline-one"
        return line_bot_service._AnswerResult("合成直接呼叫", answer_path="synthetic_capture")

    monkeypatch.setattr(line_bot_service, "_answer_stock_question_result", capture_answer)
    line_bot_service.handle_line_event(_deadline_event())
    _print_deadline_evidence("direct-call-no-ingress", state, deadlines=deadlines)
    assert deadlines == [165.0]
    assert state["telemetry"][0]["webhook_ingress_to_reply_ms"] == 0


def test_same_webhook_events_keep_one_deadline(monkeypatch, offline_deadline_handler):
    """Exercise the real signed route and BackgroundTasks, with no live HTTP listener."""
    state = offline_deadline_handler
    state["now"] = 110.0
    monkeypatch.setattr(line_webhook, "time", SimpleNamespace(monotonic=lambda: 100.0))
    secret = "offline-deadline-test-secret"
    monkeypatch.setenv("LINE_CHANNEL_SECRET", secret)
    monkeypatch.setenv("LINE_VERIFY_SIGNATURE", "true")
    deadlines = []

    def capture_answer(_question, *, deadline_monotonic, conversation_context, cohort_key=""):
        deadlines.append(deadline_monotonic)
        assert cohort_key in {"offline-deadline-one", "offline-deadline-two"}
        state["now"] = 140.0  # First event work leaves less time for the second event.
        return line_bot_service._AnswerResult("合成多事件", answer_path="synthetic_capture")

    monkeypatch.setattr(line_bot_service, "_answer_stock_question_result", capture_answer)
    body = json.dumps({"events": [_deadline_event("one"), _deadline_event("two")]}).encode("utf-8")
    response = TestClient(app).post(
        "/line/webhook", content=body,
        headers={"x-line-signature": _signature(secret, body), "content-type": "application/json"},
    )
    _print_deadline_evidence("same-webhook-two-events", state, deadlines=deadlines, http_status=response.status_code)
    assert response.status_code == 200
    assert deadlines == [145.0, 145.0]
    assert state["replies"] == ["合成多事件", "合成多事件"]


def _stub_deadline_stock_boundaries(monkeypatch, state, *, allow_model=False):
    """Real stock projection, policy and admission; synthetic read API and model boundary."""
    state["context"] = {"code": "2330", "stock_name": "台積電", "last_focus": "overview"}
    monkeypatch.setenv("QWEN_ENABLED", "true")
    monkeypatch.setenv("LINE_MODEL_INTERACTIVE_P95_MS", "25000")
    monkeypatch.setattr(line_bot_service, "now_tpe", lambda: datetime(2026, 8, 28, 16, tzinfo=ZoneInfo("Asia/Taipei")))
    monkeypatch.setattr(line_bot_service, "recent_market_date_for_eod", lambda: "2026-08-28")
    monkeypatch.setattr(line_bot_service, "is_taiwan_trading_day", lambda day: day.weekday() < 5)
    monkeypatch.setattr(
        line_bot_service, "resolve_stock_query",
        lambda query: ({"ok": True, "stock": {"code": "2330", "name": "台積電"}}
                       if query == "2330" else {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}),
    )
    monkeypatch.setattr(line_bot_service, "fetch_daily_market_data", lambda *_args, **_kwargs: {
        "status": "ok", "code": "2330", "stock": {"name": "台積電", "market": "listed"},
        "trade_date": "2026-08-28", "data_date": "2026-08-28",
        "ohlcv": {"official_trusted": True, "close": 100}, "freshness": {"ready": True},
        "valuation": {"available": False, "status": "unavailable"},
        "technical": {"available": False, "decision_ready": False},
        "data_quality": {"decision_ready": False},
    })
    monkeypatch.setattr(
        line_bot_service, "ensure_background_text_model_warmup",
        lambda: {"status": "resident", "scheduled": False},
    )
    answer = (
        "目前資料未完整帶入營收、EPS 與 ROE，所以只能說明判讀框架，不能據此形成基本面結論；"
        "待營收趨勢、獲利率、現金流與公司展望資料齊備後，才能比較是否同步改善。\n"
        "僅供資料整理，不構成投資建議。"
    )

    def model_boundary(*_args, **_kwargs):
        state["model_calls"] += 1
        state["trace"].append("model_stub")
        if not allow_model:
            raise AssertionError("queued request must be rejected before model execution")
        return answer

    monkeypatch.setattr(line_bot_service, "qwen_chat", model_boundary)
    controller = model_admission_service._AdmissionController()
    monkeypatch.setattr(model_admission_service, "_CONTROLLER", controller)
    assert line_bot_service.run_interactive_model is model_admission_service.run_interactive_model
    return controller, answer


@pytest.mark.parametrize("queue_delay,expected_path,predicted_rejects", [
    (20.0, "admission_fallback:predicted_deadline_admission_rejected", 1),
    (40.0, "deadline_budget_fallback", 0),
])
def test_handler_queue_delay_reaches_real_deadline_fallback(
    monkeypatch, offline_deadline_handler, caplog, queue_delay, expected_path, predicted_rejects,
):
    state = offline_deadline_handler
    controller, _answer = _stub_deadline_stock_boundaries(monkeypatch, state)
    caplog.set_level("INFO", logger=line_bot_service.LOGGER.name)
    line_bot_service.handle_line_event(
        _deadline_event(), webhook_ingress_monotonic=state["now"] - queue_delay,
    )
    snapshot = controller.snapshot()
    _print_deadline_evidence(
        f"real-admission-delay-{int(queue_delay)}", state, controller=snapshot, original_log=caplog.text,
    )
    assert state["model_calls"] == 0
    assert state["telemetry"][0]["answer_path"] == expected_path
    assert snapshot["predicted_deadline_rejected"] == predicted_rejects
    assert snapshot["queue_depth"] == snapshot["completed_tasks"] == 0
    assert snapshot["active_category"] is None
    assert "還沒有完整帶入營收、EPS、ROE" in state["replies"][0]
    assert "不能假裝完成基本面分析" in state["replies"][0]
    if predicted_rejects:
        assert "model task is predicted to miss its reply deadline" in caplog.text


def test_handler_no_queue_preserves_reply_bytes_and_post_reply_shadow_order(monkeypatch, offline_deadline_handler):
    state = offline_deadline_handler
    _controller, answer = _stub_deadline_stock_boundaries(monkeypatch, state, allow_model=True)
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "shadow")
    line_bot_service.handle_line_event(_deadline_event(), webhook_ingress_monotonic=state["now"])
    _print_deadline_evidence("no-queue-reply-and-shadow", state, shadow=state["shadow"])
    assert state["model_calls"] == 1  # Offline model stub only, never the real model.
    assert state["replies"][0].encode("utf-8") == answer.encode("utf-8")
    assert state["trace"] == [
        "model_stub", "reply", "finish:True", "memory_commit", "context_commit", "telemetry", "shadow",
    ]
    assert state["shadow"] == [{"stable_reply_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest()}]


@pytest.mark.parametrize(
    "validator_result,expected_path,expected_delivered",
    [
        ("pass", "canonical_candidate", True),
        (
            "reject",
            "canonical_canary_stable_fallback:candidate_validator_not_passed",
            False,
        ),
    ],
)
def test_selected_canary_is_deadline_bounded_and_fails_closed_before_reply(
    monkeypatch,
    offline_deadline_handler,
    validator_result,
    expected_path,
    expected_delivered,
):
    state = offline_deadline_handler
    _stub_deadline_stock_boundaries(monkeypatch, state, allow_model=False)
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "canary")
    monkeypatch.setattr(
        line_bot_service,
        "candidate_delivery_decision",
        lambda cohort_key: {
            "rollout": "canary",
            "authorized": True,
            "selected": True,
            "reason_codes": ["selected_for_canary"],
            "configured_canary_percentage": 5,
            "cohort_bucket": 123,
            "authorization_id": "auth-offline",
            "observed_cohort_key": cohort_key,
        },
    )
    monkeypatch.setattr(
        line_bot_service,
        "prepare_line_model_shadow",
        lambda **_kwargs: {
            "question": "基本面呢",
            "requested_scopes": ["fundamental"],
            "conversation_context": {},
            "analysis_cutoff": "2026-08-28T16:00:00+08:00",
            "request_received_at": "2026-08-28T16:00:00+08:00",
            "profile": "focused",
        },
    )

    candidate_template = {
            "validator_result": "pass",
            "candidate_can_override_referee": False,
            "model_output_sha256": "c" * 64,
            "model_output_characters": 120,
            "compacted_packet": {
                "artifact_identity": {
                    "analysis_id": "analysis-offline",
                    "canonical_answer_text_hash": "a" * 64,
                },
                "request": {"analysis_cutoff": "2026-08-28T16:00:00+08:00"},
                "referee": {
                    "immutable": True,
                    "main_status": "資料不足，先等待條件確認",
                    "reasons": ["裁判結論不可由模型覆寫"],
                },
                "events": [],
            },
            "explanation_blocks": [
                {
                    "block_type": "limitation",
                    "text_template": "基本面資料仍不完整。",
                    "evidence_ids": ["F001"],
                    "uncertainty": "high",
                    "conditions": [],
                }
            ],
            "rendered_blocks": ["基本面資料仍不完整。"],
            "used_event_ids": [],
        }
    shared_preview = render_validated_candidate_reply_preview(
        candidate_template,
        stock_code="2330",
        stock_name="台積電",
    )
    candidate_template["compacted_packet"]["artifact_identity"][
        "canonical_answer_text_hash"
    ] = shared_preview["sha256"]

    def fetch_packet(*_args, **_kwargs):
        state["trace"].append("canonical_packet")
        return {
            "packet_ready": True,
            "analysis_id": "analysis-offline",
            "canonical_answer_text": shared_preview["text"],
            "canonical_answer_text_hash": shared_preview["sha256"],
            "packet": {"contract_version": "model-fact-packet-v2"},
        }

    def run_candidate(*_args, **_kwargs):
        state["trace"].append("canonical_candidate")
        candidate = json.loads(json.dumps(candidate_template, ensure_ascii=False))
        candidate["validator_result"] = validator_result
        return candidate

    monkeypatch.setattr(line_bot_service, "fetch_canonical_question_model_packet", fetch_packet)
    monkeypatch.setattr(
        line_bot_service,
        "run_canonical_model_candidate_interactive",
        run_candidate,
    )
    monkeypatch.setattr(
        line_bot_service,
        "finalize_canonical_model_answer",
        lambda _candidate, *, cohort_key: {
            "analysis_id": "analysis-offline",
            "canonical_answer_text": shared_preview["text"],
            "canonical_answer_text_hash": shared_preview["sha256"],
            "raw_model_output_persisted": False,
        },
    )

    line_bot_service.handle_line_event(
        _deadline_event(), webhook_ingress_monotonic=state["now"],
    )

    telemetry = state["telemetry"][0]
    assert state["model_calls"] == 0
    assert telemetry["answer_path"] == expected_path
    assert telemetry["candidate_authorized"] is True
    assert telemetry["candidate_selected"] is True
    assert telemetry["candidate_delivered"] is expected_delivered
    assert state["shadow"] == []
    assert state["trace"][:2] == ["canonical_packet", "canonical_candidate"]
    assert state["trace"][-5:] == [
        "reply", "finish:True", "memory_commit", "context_commit", "telemetry",
    ]
    if expected_delivered:
        assert "主結論：資料不足，先等待條件確認" in state["replies"][0]
        assert telemetry["candidate_reply_sha256"] == hashlib.sha256(
            state["replies"][0].encode("utf-8")
        ).hexdigest()
    else:
        assert state["replies"][0] == shared_preview["text"]
        assert telemetry["candidate_reason"] == "candidate_validator_not_passed"


def test_selected_canary_with_exhausted_work_stop_never_fetches_or_runs_candidate(
    monkeypatch,
    offline_deadline_handler,
):
    state = offline_deadline_handler
    _stub_deadline_stock_boundaries(monkeypatch, state, allow_model=False)
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "canary")
    monkeypatch.setattr(
        line_bot_service,
        "candidate_delivery_decision",
        lambda _cohort_key: {
            "rollout": "canary",
            "authorized": True,
            "selected": True,
            "reason_codes": ["selected_for_canary"],
            "configured_canary_percentage": 5,
            "cohort_bucket": 123,
            "authorization_id": "auth-offline",
        },
    )
    monkeypatch.setattr(
        line_bot_service,
        "prepare_line_model_shadow",
        lambda **_kwargs: {
            "question": "基本面呢",
            "requested_scopes": ["fundamental"],
            "conversation_context": {},
            "analysis_cutoff": "2026-08-28T16:00:00+08:00",
            "request_received_at": "2026-08-28T16:00:00+08:00",
            "profile": "focused",
        },
    )
    monkeypatch.setattr(
        line_bot_service,
        "fetch_canonical_question_model_packet",
        lambda *_args, **_kwargs: pytest.fail("work-stop rejection must happen before packet I/O"),
    )
    monkeypatch.setattr(
        line_bot_service,
        "run_canonical_model_candidate_interactive",
        lambda *_args, **_kwargs: pytest.fail("work-stop rejection must happen before model execution"),
    )

    line_bot_service.handle_line_event(
        _deadline_event(), webhook_ingress_monotonic=state["now"] - 40,
    )

    telemetry = state["telemetry"][0]
    assert state["model_calls"] == 0
    assert telemetry["answer_path"] == (
        "canonical_canary_stable_fallback:candidate_work_stop_budget_exhausted"
    )
    assert telemetry["candidate_delivered"] is False
    assert state["shadow"] == []


def test_selected_canary_reuses_persisted_canonical_model_answer_without_generation(
    monkeypatch,
    offline_deadline_handler,
):
    state = offline_deadline_handler
    _stub_deadline_stock_boundaries(monkeypatch, state, allow_model=False)
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "canary")
    release_source_digest = "d" * 64
    monkeypatch.setattr(
        line_bot_service,
        "candidate_delivery_decision",
        lambda _cohort_key: {
            "rollout": "canary",
            "authorized": True,
            "selected": True,
            "reason_codes": ["selected_for_canary"],
            "configured_canary_percentage": 5,
            "cohort_bucket": 123,
            "authorization_id": "auth-offline",
            "release_source_digest": release_source_digest,
        },
    )
    monkeypatch.setattr(
        line_bot_service,
        "prepare_line_model_shadow",
        lambda **_kwargs: {
            "question": "基本面呢",
            "requested_scopes": ["fundamental"],
            "conversation_context": {},
            "analysis_cutoff": "2026-08-28T16:00:00+08:00",
            "request_received_at": "2026-08-28T16:00:00+08:00",
            "profile": "focused",
        },
    )
    persisted_text = "已封存的共用模型回答。"
    persisted_hash = hashlib.sha256(persisted_text.encode("utf-8")).hexdigest()

    def fetch_packet(*_args, **_kwargs):
        state["trace"].append("canonical_packet")
        return {
            "packet_ready": True,
            "packet": {"contract_version": "model-fact-packet-v2"},
            "analysis_id": "analysis-offline",
            "canonical_answer_text": persisted_text,
            "canonical_answer_text_hash": persisted_hash,
            "base_canonical_answer_text": "規則式穩定回答。",
            "model_answer_finalized": True,
            "model_answer_authorization_id": "auth-offline",
            "model_answer_release_source_digest": release_source_digest,
        }

    monkeypatch.setattr(line_bot_service, "fetch_canonical_question_model_packet", fetch_packet)
    monkeypatch.setattr(
        line_bot_service,
        "run_canonical_model_candidate_interactive",
        lambda *_args, **_kwargs: pytest.fail(
            "persisted answer must prevent a second model generation"
        ),
    )
    monkeypatch.setattr(
        line_bot_service,
        "finalize_canonical_model_answer",
        lambda *_args, **_kwargs: pytest.fail(
            "persisted answer must not be finalized twice"
        ),
    )

    line_bot_service.handle_line_event(
        _deadline_event(), webhook_ingress_monotonic=state["now"],
    )

    telemetry = state["telemetry"][0]
    assert state["replies"] == [persisted_text]
    assert telemetry["answer_path"] == "canonical_candidate_reused"
    assert telemetry["candidate_delivered"] is True
    assert telemetry["candidate_reply_sha256"] == persisted_hash


def test_handler_failed_reply_does_not_submit_shadow(monkeypatch, offline_deadline_handler):
    state = offline_deadline_handler
    state["fail_reply"] = True
    monkeypatch.setattr(
        line_bot_service, "_answer_stock_question_result",
        lambda *_args, **_kwargs: line_bot_service._AnswerResult(
            "合成回覆失敗", shadow_request={"request_id": "synthetic-shadow"}, answer_path="synthetic_capture",
        ),
    )
    line_bot_service.handle_line_event(_deadline_event(), webhook_ingress_monotonic=state["now"])
    _print_deadline_evidence("failed-reply-no-shadow", state)
    assert state["trace"] == ["reply", "finish:False", "telemetry"]
    assert state["shadow"] == []
    assert state["telemetry"][0]["reply_status"] == "failed"
    assert state["telemetry"][0]["error_class"] == "LineMessagingError"


def test_handle_event_commits_context_only_after_successful_line_reply(monkeypatch) -> None:
    telemetry: list[dict[str, object]] = []
    monkeypatch.setattr(
        line_bot_service,
        "append_line_reply_telemetry",
        lambda record: telemetry.append(dict(record)),
    )
    context_update = {
        "code": "2330",
        "stock_name": "台積電",
        "last_focus": "overview",
        "trade_date": "2026-08-21",
    }
    monkeypatch.setattr(
        line_bot_service,
        "_answer_stock_question_result",
        lambda *_args, **_kwargs: line_bot_service._AnswerResult(
            "完成\n僅供資料整理，不構成投資建議。",
            context_update=context_update,
        ),
    )
    monkeypatch.setattr(line_bot_service, "reply_text", lambda *_args, **_kwargs: None)
    success_event = {
        "webhookEventId": "context-success-event",
        "type": "message",
        "replyToken": "reply-success",
        "source": {"type": "user", "userId": "U-CONTEXT-SUCCESS"},
        "message": {"type": "text", "text": "分析台積電"},
    }
    success_key = line_bot_service._conversation_key(success_event)
    line_bot_service._clear_conversation_context(success_key)
    line_bot_service.handle_line_event(
        success_event,
        webhook_ingress_monotonic=time.monotonic() - 0.1,
    )
    assert line_bot_service._conversation_snapshot(success_key)["code"] == "2330"
    assert telemetry[-1]["reply_status"] == "sent"
    assert int(telemetry[-1]["webhook_ingress_to_reply_ms"]) >= 90
    line_bot_service._clear_conversation_context(success_key)

    def failed_reply(*_args, **_kwargs):
        raise line_bot_service.LineMessagingError("test failure")

    monkeypatch.setattr(line_bot_service, "reply_text", failed_reply)
    failed_event = {
        "webhookEventId": "context-failed-event",
        "type": "message",
        "replyToken": "reply-failed",
        "source": {"type": "user", "userId": "U-CONTEXT-FAILED"},
        "message": {"type": "text", "text": "分析台積電"},
    }
    failed_key = line_bot_service._conversation_key(failed_event)
    line_bot_service._clear_conversation_context(failed_key)
    line_bot_service.handle_line_event(failed_event)
    assert line_bot_service._conversation_snapshot(failed_key) == {}
    assert telemetry[-1]["reply_status"] == "failed"
    assert telemetry[-1]["error_class"] == "LineMessagingError"


def test_free_form_multi_turn_dialogue_keeps_stock_then_switches_to_market(monkeypatch) -> None:
    def fake_resolve(query: str) -> dict[str, object]:
        if query in {"台積電最近怎麼樣", "2330", "那對台積電的風險呢"}:
            return {"ok": True, "stock": {"code": "2330", "name": "台積電"}}
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    monkeypatch.setattr(line_bot_service, "fetch_daily_market_data", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(line_bot_service, "_compact_daily", lambda _payload: _focused_daily_facts())
    monkeypatch.setattr(
        line_bot_service,
        "fetch_market_brief",
        lambda _question: {
            "ok": True,
            "status": "ok",
            "reference_date": "2026-08-27",
            "topic_match_required": False,
            "topic_match": True,
            "matched_events": [],
            "external_event_context": {
                "available": True,
                "events": [
                    {
                        "event_date": "2026-08-26",
                        "publisher": "經濟部",
                        "title": "產業政策最新說明",
                    }
                ],
            },
        },
    )
    monkeypatch.setenv("QWEN_ENABLED", "false")

    context: dict[str, object] = {}
    turns = []
    for question in (
        "台積電最近怎麼樣",
        "如果營收變差呢",
        "我沒持有，比較偏波段",
        "那要注意哪個條件",
        "最近市場在炒什麼",
        "那對台積電的風險呢",
    ):
        result = line_bot_service._answer_stock_question_result(
            question,
            conversation_context=context,
        )
        turns.append(result)
        context = dict(result.context_update or context)

    assert turns[1].context_update["code"] == "2330"
    assert turns[1].context_update["last_focus"] == "fundamentals"
    assert "如果營收轉差" in turns[1].text
    assert turns[2].context_update["investment_horizon"] == "swing"
    assert turns[2].context_update["position_state"] == "not_holding"
    assert turns[3].context_update["last_focus"] == "risk"
    assert turns[4].context_update["last_mode"] == "market"
    assert "2026-08-26" in turns[4].text
    assert "經濟部" in turns[4].text
    assert turns[5].context_update["code"] == "2330"
    assert turns[5].context_update["last_focus"] == "risk"
    assert all("請告訴我公司名稱或四碼代號" not in turn.text for turn in turns)


def test_recent_policy_question_without_symbol_uses_market_brief(monkeypatch) -> None:
    monkeypatch.setattr(
        line_bot_service,
        "resolve_stock_query",
        lambda _query: (_ for _ in ()).throw(AssertionError("market question must not require a symbol")),
    )
    monkeypatch.setattr(
        line_bot_service,
        "fetch_market_brief",
        lambda _question: {
            "ok": True,
            "status": "ok",
            "reference_date": "2026-08-27",
            "topic_match_required": True,
            "topic_match": False,
            "matched_events": [],
            "external_event_context": {"available": True, "events": []},
        },
    )
    monkeypatch.setenv("QWEN_ENABLED", "false")

    result = line_bot_service._answer_stock_question_result("川普這個消息會影響哪些類股？")

    assert "沒有找到" in result.text
    assert "文字、截圖或連結" in result.text
    assert "股票名稱或四碼" not in result.text
    assert result.context_update["last_mode"] == "market"


def test_ambiguous_investment_turn_asks_a_useful_question_not_only_for_symbol(monkeypatch) -> None:
    monkeypatch.setattr(
        line_bot_service,
        "resolve_stock_query",
        lambda _query: {"ok": False, "status": "not_found", "candidates": [], "suggestions": []},
    )
    monkeypatch.setenv("QWEN_ENABLED", "false")

    answer = line_bot_service.answer_stock_question("這件事情怎麼看？")

    assert "整體台股、某個產業題材，還是某一檔股票" in answer
    assert "不用照固定口令" in answer
    assert "請告訴我公司名稱或四碼代號" not in answer


def test_combined_market_close_news_and_geopolitical_question_bypasses_stock_clarification(
    monkeypatch,
) -> None:
    question = "昨日收盤幫我評估整體走勢及抓取今日重大財經新聞、美伊現況及股市分析"
    captured: list[str] = []

    monkeypatch.setattr(
        line_bot_service,
        "resolve_stock_query",
        lambda _query: (_ for _ in ()).throw(
            AssertionError("explicit market question must not require a stock symbol")
        ),
    )
    monkeypatch.setattr(
        line_bot_service,
        "fetch_market_brief",
        lambda query: captured.append(query)
        or {
            "ok": True,
            "status": "ok",
            "reference_date": "2026-08-29",
            "topic_match_required": True,
            "topic_match": True,
            "matched_events": [
                {
                    "event_date": "2026-08-29",
                    "publisher": "The White House",
                    "title": "Official Iran and Strait of Hormuz update",
                }
            ],
            "external_event_context": {"available": True, "events": []},
            "global_market_context": {"available": True, "market_date": "2026-08-28"},
        },
    )
    monkeypatch.setenv("QWEN_ENABLED", "false")

    result = line_bot_service._answer_stock_question_result(
        question,
        conversation_context={"code": "2382", "stock_name": "廣達", "last_focus": "overview"},
    )

    assert captured == [question]
    assert result.context_update["last_mode"] == "market"
    assert "The White House" in result.text
    assert "無法確認你指的是哪一檔股票" not in result.text


def test_semantic_router_can_recover_an_unlisted_natural_follow_up(monkeypatch) -> None:
    def fake_resolve(query: str) -> dict[str, object]:
        if query == "2330":
            return {"ok": True, "stock": {"code": "2330", "name": "台積電"}}
        return {"ok": False, "status": "not_found", "candidates": [], "suggestions": []}

    monkeypatch.setattr(line_bot_service, "resolve_stock_query", fake_resolve)
    monkeypatch.setattr(line_bot_service, "fetch_daily_market_data", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(line_bot_service, "_compact_daily", lambda _payload: _focused_daily_facts())
    monkeypatch.setattr(
        line_bot_service,
        "classify_ambiguous_turn",
        lambda *_args, **_kwargs: type("Route", (), {"intent": "active_stock_follow_up"})(),
    )
    # The router itself is stubbed above; keep this unit test independent of a
    # live 27B inference server and verify answer generation separately.
    monkeypatch.setenv("QWEN_ENABLED", "false")

    result = line_bot_service._answer_stock_question_result(
        "這樣還撐得住嗎？",
        conversation_context={"code": "2330", "stock_name": "台積電", "last_focus": "risk"},
    )

    assert result.context_update["code"] == "2330"
    assert "無法確認你指的是哪一檔股票" not in result.text
