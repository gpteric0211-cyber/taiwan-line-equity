from __future__ import annotations

import sys
import time
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.line_memory_config import LineMemorySettings, line_memory_settings  # noqa: E402
from repository import line_conversation_repository  # noqa: E402
from services import conversation_compaction_service  # noqa: E402
from services import line_bot_service  # noqa: E402
from services.conversation_compaction_service import compact_conversation  # noqa: E402
from services.conversation_memory_service import (  # noqa: E402
    ConversationMemoryService,
    conversation_memory_service,
    reset_conversation_memory_service_for_tests,
)


def _settings(
    tmp_path: Path,
    *,
    trigger: int = 8,
    raw_retention_seconds: int = 86400,
    summary_retention_seconds: int = 30 * 86400,
) -> LineMemorySettings:
    return LineMemorySettings(
        storage="sqlite",
        database_path=tmp_path / "memory.sqlite3",
        key_file=tmp_path / "memory.key",
        channel_namespace="test-channel",
        raw_retention_seconds=raw_retention_seconds,
        summary_retention_seconds=summary_retention_seconds,
        recent_exchange_limit=8,
        prompt_character_budget=6000,
        compaction_trigger=trigger,
        compaction_batch_size=max(trigger, 3),
        compaction_timeout_seconds=60,
        privacy_notice_version="2026-08-28",
        long_term_approved=True,
    )


def _event(
    event_id: str,
    user_id: str,
    *,
    timestamp: int,
    message_id: str = "",
    source_type: str = "user",
    group_id: str = "",
) -> dict[str, object]:
    source: dict[str, str] = {"type": source_type, "userId": user_id}
    if group_id:
        source["groupId"] = group_id
    message: dict[str, str] = {"type": "text", "text": "測試"}
    if message_id:
        message["id"] = message_id
    return {
        "webhookEventId": event_id,
        "timestamp": timestamp,
        "type": "message",
        "replyToken": f"reply-{event_id}",
        "source": source,
        "message": message,
    }


def _commit(
    service: ConversationMemoryService,
    event: dict[str, object],
    *,
    question: str,
    answer: str,
    code: str,
    name: str,
    trade_date: str = "2026-08-27",
) -> None:
    assert service.commit_after_delivery(
        event,
        user_text=question,
        assistant_text=answer,
        state={
            "code": code,
            "stock_name": name,
            "last_focus": "overview",
            "last_mode": "stock",
            "trade_date": trade_date,
            "recent_user_questions": [question],
        },
        message_type="text",
    )


def test_encrypted_memory_survives_restart_and_contains_both_sides(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = ConversationMemoryService(settings)
    event = _event("event-1", "U-ONE", timestamp=1000, message_id="message-1")
    _commit(
        first,
        event,
        question="幫我看台積電",
        answer="台積電目前先觀察量價。",
        code="2330",
        name="台積電",
    )

    restarted = ConversationMemoryService(settings)
    context = restarted.context_for_event(
        _event("event-2", "U-ONE", timestamp=2000, message_id="message-2")
    )

    assert context["code"] == "2330"
    assert context["last_assistant_answer"] == "台積電目前先觀察量價。"
    assert context["recent_exchanges"][-1]["user"] == "幫我看台積電"
    assert context["memory_contract"]["current_market_facts_override"] is True
    database_bytes = settings.database_path.read_bytes()
    assert "幫我看台積電".encode("utf-8") not in database_bytes
    assert "台積電目前先觀察量價".encode("utf-8") not in database_bytes


def test_users_and_group_scopes_are_isolated_without_raw_line_ids(tmp_path: Path) -> None:
    service = ConversationMemoryService(_settings(tmp_path))
    private = _event("private", "U-ONE", timestamp=1000)
    other_user = _event("other", "U-TWO", timestamp=1100)
    group = _event(
        "group",
        "U-ONE",
        timestamp=1200,
        source_type="group",
        group_id="G-ONE",
    )
    _commit(service, private, question="台積電", answer="私人回答", code="2330", name="台積電")
    _commit(service, group, question="聯發科", answer="群組回答", code="2454", name="聯發科")

    assert service.context_for_event(other_user) == {}
    assert service.context_for_event(private)["code"] == "2330"
    assert service.context_for_event(group)["code"] == "2454"
    private_identity = service.event_context(private)
    group_identity = service.event_context(group)
    assert private_identity.conversation_key != group_identity.conversation_key
    assert "U-ONE" not in str(private_identity.identity)
    assert "G-ONE" not in str(group_identity.identity)


def test_group_event_without_user_id_never_persists_or_reuses_history(tmp_path: Path) -> None:
    service = ConversationMemoryService(_settings(tmp_path))
    anonymous = _event(
        "anonymous-1",
        "",
        timestamp=1000,
        source_type="group",
        group_id="G-ONE",
    )
    assert service.event_context(anonymous).persistent_allowed is False
    assert not service.commit_after_delivery(
        anonymous,
        user_text="這檔呢",
        assistant_text="無法辨識個人範圍",
        state={"code": "2330", "stock_name": "台積電"},
        message_type="text",
    )
    assert service.context_for_event(anonymous) == {}


def test_event_redelivery_is_idempotent_and_failed_event_can_retry(tmp_path: Path) -> None:
    service = ConversationMemoryService(_settings(tmp_path))
    event = _event("same-event", "U-ONE", timestamp=1000)
    assert service.begin_event(event) is True
    assert service.begin_event(event) is False
    service.finish_event(event, delivered=False)
    assert service.begin_event(event) is True
    service.finish_event(event, delivered=True)
    assert service.begin_event(event) is False


def test_older_event_cannot_overwrite_newer_session_state(tmp_path: Path) -> None:
    service = ConversationMemoryService(_settings(tmp_path))
    newer = _event("newer", "U-ONE", timestamp=2000)
    older = _event("older", "U-ONE", timestamp=1000)
    _commit(service, newer, question="看聯發科", answer="新回答", code="2454", name="聯發科")
    _commit(service, older, question="看台積電", answer="舊回答", code="2330", name="台積電")

    context = service.context_for_event(_event("next", "U-ONE", timestamp=3000))
    assert context["code"] == "2454"
    assert context["stock_name"] == "聯發科"


def test_unsend_and_principal_delete_remove_memory(tmp_path: Path) -> None:
    service = ConversationMemoryService(_settings(tmp_path))
    private = _event("private", "U-ONE", timestamp=1000, message_id="message-private")
    group = _event(
        "group",
        "U-ONE",
        timestamp=1100,
        message_id="message-group",
        source_type="group",
        group_id="G-ONE",
    )
    _commit(service, private, question="台積電", answer="私人回答", code="2330", name="台積電")
    unsend = {
        "type": "unsend",
        "timestamp": 1200,
        "source": private["source"],
        "unsend": {"messageId": "message-private"},
    }
    assert service.suppress_unsent(unsend) == 1
    assert service.context_for_event(_event("after-unsend", "U-ONE", timestamp=1300)) == {}

    _commit(service, private, question="台積電", answer="私人回答", code="2330", name="台積電")
    _commit(service, group, question="聯發科", answer="群組回答", code="2454", name="聯發科")
    assert service.clear_principal(private) == 2
    assert service.context_for_event(private) == {}
    assert service.context_for_event(group) == {}


def test_compaction_creates_global_and_stock_memory_without_losing_recent_turns(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        conversation_compaction_service,
        "qwen_chat",
        lambda *_args, **_kwargs: (
            '{"summary":"使用者持續關注台積電 2330，歷史資料日 2026-08-27。",'
            '"important_corrections":[],"unresolved_questions":["量價是否改善"],'
            '"stock_summaries":[{"code":"2330","summary":"台積電 2330 的歷史討論日為 2026-08-27。",'
            '"latest_trade_date":"2026-08-27"}]}'
        ),
    )
    service = ConversationMemoryService(_settings(tmp_path, trigger=2))
    first = _event("event-1", "U-ONE", timestamp=1000)
    second = _event("event-2", "U-ONE", timestamp=2000)
    _commit(service, first, question="台積電 2330 怎麼看", answer="先看 2026-08-27 量價。", code="2330", name="台積電")
    _commit(service, second, question="那風險呢", answer="仍要留意量價。", code="2330", name="台積電")

    context = service.context_for_event(_event("event-3", "U-ONE", timestamp=3000))
    assert "持續關注台積電" in context["rolling_summary"]
    assert context["stock_memory"]["stock_code"] == "2330"
    assert len(context["recent_exchanges"]) == 2


def test_compaction_rejects_invented_numbers_and_codes(monkeypatch) -> None:
    monkeypatch.setattr(
        conversation_compaction_service,
        "qwen_chat",
        lambda *_args, **_kwargs: (
            '{"summary":"台積電目標價 9999", "important_corrections":[],'
            '"unresolved_questions":[],"stock_summaries":[{"code":"8888",'
            '"summary":"新股票 8888","latest_trade_date":"2026-08-27"}]}'
        ),
    )
    result = compact_conversation(
        existing_summary=None,
        exchanges=[
            {
                "exchange_id": 1,
                "stock_code": "2330",
                "trade_date": "2026-08-27",
                "user": "台積電 2330 怎麼看",
                "assistant": "先觀察量價。",
            }
        ],
    )
    assert result.ok is True
    assert result.reason == (
        "deterministic_fallback:summary_validation_failed:unsupported_numeric_tokens:1"
    )
    assert "9999" not in result.summary["summary"]
    assert "8888" not in result.stock_summaries
    assert result.stock_summaries["2330"]["stock_code"] == "2330"


def test_compaction_model_failure_uses_source_only_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        conversation_compaction_service,
        "qwen_chat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            conversation_compaction_service.QwenClientError("offline")
        ),
    )

    result = compact_conversation(
        existing_summary=None,
        exchanges=[
            {
                "exchange_id": 1,
                "stock_code": "2330",
                "trade_date": "2026-08-27",
                "user": "台積電 2330 怎麼看",
                "assistant": "先觀察 2026-08-27 的量價。",
            }
        ],
    )

    assert result.ok is True
    assert result.model_id == "deterministic-line-memory-v1"
    assert result.reason.startswith("deterministic_fallback:model_error:")
    assert "台積電 2330 怎麼看" in result.summary["summary"]


def test_retention_is_clamped_until_privacy_notice_is_approved(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MEMORY_STORAGE", "sqlite")
    monkeypatch.setenv("LINE_MEMORY_RAW_RETENTION_SECONDS", str(30 * 86400))
    monkeypatch.setenv("LINE_MEMORY_SUMMARY_RETENTION_SECONDS", str(90 * 86400))
    monkeypatch.setenv("LINE_MEMORY_PRIVACY_NOTICE_VERSION", "")
    monkeypatch.setenv("LINE_MEMORY_LONG_TERM_APPROVED", "false")
    settings = line_memory_settings()
    assert settings.raw_retention_seconds == 86400
    assert settings.summary_retention_seconds == 86400
    assert settings.long_term_approved is False


def test_configured_summary_retention_is_30_days(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MEMORY_STORAGE", "sqlite")
    monkeypatch.setenv("LINE_MEMORY_RAW_RETENTION_SECONDS", "86400")
    monkeypatch.setenv("LINE_MEMORY_SUMMARY_RETENTION_SECONDS", str(30 * 86400))
    monkeypatch.setenv("LINE_MEMORY_PRIVACY_NOTICE_VERSION", "2026-08-28")
    monkeypatch.setenv("LINE_MEMORY_LONG_TERM_APPROVED", "true")

    settings = line_memory_settings()

    assert settings.raw_retention_seconds == 86400
    assert settings.summary_retention_seconds == 30 * 86400
    assert settings.long_term_approved is True


def test_latest_answer_survives_raw_expiry_until_30_day_session_expiry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    clock = [1_800_000_000.0]
    monkeypatch.setattr(line_conversation_repository.time, "time", lambda: clock[0])
    service = ConversationMemoryService(_settings(tmp_path))
    event = _event("sparse-first", "U-SPARSE", timestamp=1000)
    _commit(
        service,
        event,
        question="幫我看台積電",
        answer="台積電先觀察量價是否改善。",
        code="2330",
        name="台積電",
    )
    assert service.repository is not None

    service.repository.purge_expired(now=clock[0] + 86401)
    clock[0] += 2 * 86400
    context = service.context_for_event(
        _event("sparse-follow-up", "U-SPARSE", timestamp=2000)
    )

    assert context["code"] == "2330"
    assert context["last_assistant_answer"] == "台積電先觀察量價是否改善。"
    assert "recent_exchanges" not in context

    clock[0] += 29 * 86400
    assert service.context_for_event(
        _event("sparse-expired", "U-SPARSE", timestamp=3000)
    ) == {}


def test_new_notice_version_migrates_active_24_hour_session_once(
    monkeypatch,
    tmp_path: Path,
) -> None:
    clock = [1_800_000_000.0]
    monkeypatch.setattr(line_conversation_repository.time, "time", lambda: clock[0])
    old_settings = LineMemorySettings(
        **{
            **_settings(tmp_path).__dict__,
            "summary_retention_seconds": 86400,
            "privacy_notice_version": "",
            "long_term_approved": False,
        }
    )
    old_service = ConversationMemoryService(old_settings)
    event = _event("policy-old", "U-POLICY", timestamp=1000)
    _commit(
        old_service,
        event,
        question="分析台積電",
        answer="先前實際送出的回答。",
        code="2330",
        name="台積電",
    )
    old_memory = old_service.event_context(event)
    assert old_service.repository is not None
    assert old_memory.identity is not None
    assert old_service.repository.save_session(
        old_memory.identity,
        {
            "code": "2330",
            "stock_name": "台積電",
            "recent_user_questions": ["分析台積電"],
        },
        event_timestamp=1001,
        event_key="legacy-state-without-answer",
    )

    clock[0] += 3600
    migrated_service = ConversationMemoryService(_settings(tmp_path))
    assert migrated_service.repository is not None
    context = migrated_service.context_for_event(
        _event("policy-new", "U-POLICY", timestamp=2000)
    )
    assert context["last_assistant_answer"] == "先前實際送出的回答。"

    with migrated_service.repository._connection() as conn:
        first_expiry = float(
            conn.execute("SELECT expires_at FROM line_memory_session").fetchone()[0]
        )
        notice_version = str(
            conn.execute(
                "SELECT privacy_notice_version FROM line_memory_subject"
            ).fetchone()[0]
        )
    assert notice_version == "2026-08-28"
    assert first_expiry == clock[0] + 30 * 86400

    clock[0] += 3600
    restarted = ConversationMemoryService(_settings(tmp_path))
    assert restarted.repository is not None
    with restarted.repository._connection() as conn:
        second_expiry = float(
            conn.execute("SELECT expires_at FROM line_memory_session").fetchone()[0]
        )
    assert second_expiry == first_expiry


def test_compacted_summary_survives_raw_expiry_and_remains_user_isolated(
    monkeypatch,
    tmp_path: Path,
) -> None:
    clock = [1_800_000_000.0]
    monkeypatch.setattr(line_conversation_repository.time, "time", lambda: clock[0])
    monkeypatch.setattr(
        conversation_compaction_service,
        "qwen_chat",
        lambda *_args, **_kwargs: (
            '{"summary":"使用者持續關注台積電 2330 的量價與風險，歷史資料日 2026-08-27。",'
            '"important_corrections":[],"unresolved_questions":["量價是否改善"],'
            '"stock_summaries":[{"code":"2330","summary":"台積電 2330 的歷史討論重點是量價與風險，資料日 2026-08-27。",'
            '"latest_trade_date":"2026-08-27"}]}'
        ),
    )
    service = ConversationMemoryService(_settings(tmp_path, trigger=2))
    _commit(
        service,
        _event("summary-1", "U-SUMMARY", timestamp=1000),
        question="台積電 2330 怎麼看",
        answer="先看 2026-08-27 的量價。",
        code="2330",
        name="台積電",
    )
    _commit(
        service,
        _event("summary-2", "U-SUMMARY", timestamp=2000),
        question="那風險呢",
        answer="仍要留意量價是否改善。",
        code="2330",
        name="台積電",
    )
    assert service.repository is not None
    assert service.repository.counts()["summaries"] == 2

    service.repository.purge_expired(now=clock[0] + 86401)
    clock[0] += 29 * 86400
    context = service.context_for_event(
        _event("summary-follow-up", "U-SUMMARY", timestamp=3000)
    )

    assert "持續關注台積電" in context["rolling_summary"]
    assert context["stock_memory"]["stock_code"] == "2330"
    assert "recent_exchanges" not in context
    assert service.context_for_event(
        _event("other-user", "U-OTHER", timestamp=3000)
    ) == {}

    clock[0] += 2 * 86400
    assert service.context_for_event(
        _event("summary-expired", "U-SUMMARY", timestamp=4000)
    ) == {}


def test_local_model_prompt_receives_bounded_rolling_summary(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_qwen(system_prompt: str, user_prompt: str, **_kwargs) -> str:
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return (
            "前面談到量價需要連續確認，這一題可以接著看成交量是否配合價格結構。"
            "你想先談量能，還是支撐位置？\n僅供資料整理，不構成投資建議。"
        )

    monkeypatch.setenv("QWEN_ENABLED", "true")
    monkeypatch.setattr(line_bot_service, "qwen_chat", fake_qwen)
    result = line_bot_service._answer_general_investment_question(
        "那前面提到的量價要怎麼接著看？",
        deadline_monotonic=None,
        conversation_context={
            "rolling_summary": "使用者前面關注量價與止跌確認。",
            "recent_user_questions": ["量價怎麼看"],
            "memory_contract": {
                "historical_context_only": True,
                "current_market_facts_override": True,
            },
        },
    )

    assert "前面談到量價" in result.text
    assert "使用者前面關注量價與止跌確認" in captured["user"]
    assert '"historical_context_only":true' in captured["user"]


def test_privacy_notice_is_available_as_a_normal_conversation_command() -> None:
    result = line_bot_service._answer_stock_question_result("隱私告知")

    assert "逐字問答最長保存 24 小時" in result.text
    assert "摘要最長保存 30 天" in result.text
    assert "刪除我的資料" in result.text


def test_line_gateway_uses_persisted_assistant_answer_after_service_restart(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LINE_MEMORY_STORAGE", "sqlite")
    monkeypatch.setenv("LINE_MEMORY_DB_PATH", str(tmp_path / "gateway.sqlite3"))
    monkeypatch.setenv("LINE_MEMORY_KEY_FILE", str(tmp_path / "gateway.key"))
    monkeypatch.setenv("LINE_MEMORY_CHANNEL_NAMESPACE", "gateway-test")
    monkeypatch.setenv("LINE_MEMORY_COMPACTION_TRIGGER", "8")
    reset_conversation_memory_service_for_tests()
    captured_contexts: list[dict[str, object]] = []

    def fake_answer(question: str, **kwargs):
        context = dict(kwargs.get("conversation_context") or {})
        captured_contexts.append(context)
        return line_bot_service._AnswerResult(
            "第一輪實際送出的回答" if len(captured_contexts) == 1 else "第二輪有接到上文",
            context_update={
                "code": "2330",
                "stock_name": "台積電",
                "last_focus": "overview",
                "last_mode": "stock",
                "trade_date": "2026-08-27",
                "recent_user_questions": [question],
            },
        )

    monkeypatch.setattr(line_bot_service, "_answer_stock_question_result", fake_answer)
    monkeypatch.setattr(line_bot_service, "reply_text", lambda *_args, **_kwargs: None)
    source = {"type": "user", "userId": "U-GATEWAY-RESTART"}
    first = {
        "webhookEventId": "gateway-first",
        "timestamp": 1000,
        "type": "message",
        "replyToken": "reply-first",
        "source": source,
        "message": {"id": "message-first", "type": "text", "text": "分析台積電"},
    }
    second = {
        "webhookEventId": "gateway-second",
        "timestamp": 2000,
        "type": "message",
        "replyToken": "reply-second",
        "source": source,
        "message": {"id": "message-second", "type": "text", "text": "那風險呢"},
    }
    key = line_bot_service._conversation_key(first)
    try:
        line_bot_service._clear_conversation_context(key)
        line_bot_service.handle_line_event(first)
        reset_conversation_memory_service_for_tests()
        line_bot_service._clear_conversation_context(key)
        line_bot_service.handle_line_event(second)

        assert captured_contexts[0] == {}
        assert captured_contexts[1]["code"] == "2330"
        assert captured_contexts[1]["last_assistant_answer"] == "第一輪實際送出的回答"
        exchanges = captured_contexts[1]["recent_exchanges"]
        assert exchanges[-1]["user"] == "分析台積電"
        assert exchanges[-1]["assistant"] == "第一輪實際送出的回答"
    finally:
        reset_conversation_memory_service_for_tests()
        line_bot_service._clear_conversation_context(key)


def test_line_gateway_does_not_persist_when_line_reply_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LINE_MEMORY_STORAGE", "sqlite")
    monkeypatch.setenv("LINE_MEMORY_DB_PATH", str(tmp_path / "failed.sqlite3"))
    monkeypatch.setenv("LINE_MEMORY_KEY_FILE", str(tmp_path / "failed.key"))
    monkeypatch.setenv("LINE_MEMORY_CHANNEL_NAMESPACE", "failed-test")
    reset_conversation_memory_service_for_tests()
    event = {
        "webhookEventId": "gateway-failed",
        "timestamp": 1000,
        "type": "message",
        "replyToken": "reply-failed",
        "source": {"type": "user", "userId": "U-GATEWAY-FAILED"},
        "message": {"id": "message-failed", "type": "text", "text": "分析台積電"},
    }
    monkeypatch.setattr(
        line_bot_service,
        "_answer_stock_question_result",
        lambda *_args, **_kwargs: line_bot_service._AnswerResult(
            "不應寫入",
            context_update={"code": "2330", "stock_name": "台積電"},
        ),
    )
    monkeypatch.setattr(
        line_bot_service,
        "reply_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            line_bot_service.LineMessagingError("expected failure")
        ),
    )
    line_bot_service.handle_line_event(event)
    reset_conversation_memory_service_for_tests()
    assert conversation_memory_service().context_for_event(event) == {}
    reset_conversation_memory_service_for_tests()


def test_historical_memory_cannot_authorize_old_numbers_as_current_facts() -> None:
    facts = {
        "status": "ok",
        "code": "2330",
        "stock": {"name": "台積電"},
        "trade_date": "2026-08-27",
        "evidence_summary": {},
        "referee": {"decision_ready": False},
        "verified_claims": [],
    }
    projected = line_bot_service._facts_for_focus(
        facts,
        "overview",
        continued=True,
        previous_focus="overview",
        conversation_memory={
            "recent_exchanges": [
                {
                    "user": "之前價格呢",
                    "assistant": "歷史回答提過價格 9999",
                    "stock_code": "2330",
                    "trade_date": "2026-08-20",
                }
            ],
            "memory_contract": {
                "historical_context_only": True,
                "current_market_facts_override": True,
            },
        },
    )
    assert projected["conversation"]["recent_exchanges"][0]["assistant"].endswith("9999")
    assert not line_bot_service._answer_numbers_are_grounded(
        "目前價格是 9999。",
        projected,
        "那目前呢",
    )


def test_delete_all_user_command_is_distinct_from_scope_reset() -> None:
    delete_all = line_bot_service._answer_stock_question_result("刪除我的資料")
    scope_only = line_bot_service._answer_stock_question_result("清除對話")
    assert delete_all.delete_all_memory is True
    assert delete_all.clear_context is True
    assert scope_only.delete_all_memory is False
    assert scope_only.clear_context is True
