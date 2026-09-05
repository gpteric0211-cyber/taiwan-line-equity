from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from adapter.qwen_local import QwenClientError, qwen_chat
from core.line_bot_config import env_text
from services.model_admission_service import ModelAdmissionError, run_maintenance_model


SUMMARY_VERSION = "line-memory-summary-v1"
DETERMINISTIC_SUMMARY_MODEL_ID = "deterministic-line-memory-v1"
_NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?%?")


@dataclass(frozen=True)
class ConversationCompactionResult:
    ok: bool
    summary: dict[str, Any]
    stock_summaries: dict[str, dict[str, Any]]
    model_id: str
    reason: str = ""


def _json_object(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", str(text or ""), flags=re.DOTALL)
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _bounded_strings(value: Any, *, maximum_items: int, maximum_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        " ".join(str(item).split())[:maximum_chars]
        for item in value[:maximum_items]
        if str(item).strip()
    ]


def _numeric_tokens(value: str) -> set[str]:
    return {match.group(0).replace(",", "") for match in _NUMBER_RE.finditer(value)}


def _deterministic_summary(
    *,
    existing_summary: dict[str, Any] | None,
    source_rows: list[dict[str, Any]],
    reason: str,
) -> ConversationCompactionResult:
    """Keep source-only continuity when the local summarizer is unavailable.

    This fallback deliberately copies bounded historical text instead of making
    any inference.  It prevents an Ollama restart or malformed model response
    from silently discarding all older context when raw exchanges expire.
    """

    existing_text = " ".join(str((existing_summary or {}).get("summary") or "").split())
    parts = [existing_text] if existing_text else []
    per_stock: dict[str, list[str]] = {}
    latest_dates: dict[str, str] = {}
    for row in source_rows:
        code = str(row.get("stock_code") or "")
        trade_date = str(row.get("trade_date") or "")[:16]
        user = " ".join(str(row.get("user") or "").split())[:500]
        assistant = " ".join(str(row.get("assistant") or "").split())[:700]
        label = " ".join(item for item in (code, trade_date) if item)
        excerpt = f"{label} 使用者問：{user}；先前回覆：{assistant}".strip()
        parts.append(excerpt)
        if re.fullmatch(r"\d{4}", code):
            per_stock.setdefault(code, []).append(excerpt)
            if trade_date:
                latest_dates[code] = max(latest_dates.get(code, ""), trade_date)

    summary_text = " ".join(parts)[-2400:]
    if not summary_text:
        return ConversationCompactionResult(
            False, {}, {}, DETERMINISTIC_SUMMARY_MODEL_ID, reason
        )
    summary = {
        "summary": summary_text,
        "important_corrections": _bounded_strings(
            (existing_summary or {}).get("important_corrections"),
            maximum_items=8,
            maximum_chars=300,
        ),
        "unresolved_questions": _bounded_strings(
            (existing_summary or {}).get("unresolved_questions"),
            maximum_items=8,
            maximum_chars=300,
        ),
    }
    stock_summaries = {
        code: {
            "stock_code": code,
            "summary": " ".join(rows)[-1400:],
            "latest_trade_date": latest_dates.get(code, ""),
        }
        for code, rows in per_stock.items()
    }
    return ConversationCompactionResult(
        True,
        summary,
        stock_summaries,
        DETERMINISTIC_SUMMARY_MODEL_ID,
        f"deterministic_fallback:{reason}",
    )


def _validate_summary_payload(
    payload: dict[str, Any],
    *,
    allowed_numbers: set[str],
    allowed_stock_codes: set[str],
) -> tuple[
    tuple[dict[str, Any], dict[str, dict[str, Any]]] | None,
    str,
]:
    summary_text = " ".join(str(payload.get("summary") or "").split())[:2400]
    if not summary_text:
        return None, "missing_summary"
    normalized: dict[str, Any] = {
        "summary": summary_text,
        "important_corrections": _bounded_strings(
            payload.get("important_corrections"), maximum_items=8, maximum_chars=300
        ),
        "unresolved_questions": _bounded_strings(
            payload.get("unresolved_questions"), maximum_items=8, maximum_chars=300
        ),
    }
    stock_summaries: dict[str, dict[str, Any]] = {}
    rows = payload.get("stock_summaries")
    if isinstance(rows, list):
        for row in rows[:12]:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "")
            text = " ".join(str(row.get("summary") or "").split())[:1400]
            if code not in allowed_stock_codes or not text:
                continue
            stock_summaries[code] = {
                "stock_code": code,
                "summary": text,
                "latest_trade_date": str(row.get("latest_trade_date") or "")[:16],
            }
    serialized = json.dumps(
        {"summary": normalized, "stock_summaries": stock_summaries},
        ensure_ascii=False,
        sort_keys=True,
    )
    unsupported_numbers = _numeric_tokens(serialized) - allowed_numbers
    if unsupported_numbers:
        return None, f"unsupported_numeric_tokens:{len(unsupported_numbers)}"
    return (normalized, stock_summaries), ""


def compact_conversation(
    *,
    existing_summary: dict[str, Any] | None,
    exchanges: list[dict[str, Any]],
    timeout_seconds: float = 18,
) -> ConversationCompactionResult:
    model_id = env_text(
        "QWEN_MEMORY_MODEL_ID",
        env_text("QWEN_MODEL_ID", "taiwan-stock-qwen"),
    )
    if not exchanges:
        return ConversationCompactionResult(False, {}, {}, model_id, "no_exchanges")
    source_rows = [
        {
            "exchange_id": int(row.get("exchange_id") or 0),
            "stock_code": str(row.get("stock_code") or ""),
            "trade_date": str(row.get("trade_date") or ""),
            "user": str(row.get("user") or "")[:1000],
            "assistant": str(row.get("assistant") or "")[:1600],
        }
        for row in exchanges
    ]
    source = {
        "existing_summary": {
            key: (existing_summary or {}).get(key)
            for key in ("summary", "important_corrections", "unresolved_questions")
        },
        "exchanges": source_rows,
    }
    source_json = json.dumps(source, ensure_ascii=False, separators=(",", ":"))
    grounding_source = json.dumps(
        {
            "existing_summary": source["existing_summary"],
            "dialogue": [
                {
                    "stock_code": row["stock_code"],
                    "trade_date": row["trade_date"],
                    "user": row["user"],
                    "assistant": row["assistant"],
                }
                for row in source_rows
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    system_prompt = (
        "你只負責壓縮繁體中文LINE投資對話，不回答使用者，也不重新分析股票。"
        "只能使用輸入中已存在的內容；股票代號、價格、成本、日期、百分比、否定語、推估標記與舊結論不得改寫或新增。"
        "市場數字與舊結論一律視為有日期的歷史內容，不可稱為現在或最新。"
        "輸出一個JSON物件，格式為："
        '{"summary":"重點摘要","important_corrections":["使用者修正"],'
        '"unresolved_questions":["未完成問題"],"stock_summaries":'
        '[{"code":"四碼代號","summary":"該股歷史摘要","latest_trade_date":"YYYY-MM-DD"}]}。'
        "整個JSON盡量控制在350個繁體中文字內，每個個股摘要不超過120字。"
        "不要輸出Markdown或其他文字。"
    )
    try:
        admission = run_maintenance_model(
            lambda: qwen_chat(
                system_prompt,
                source_json,
                timeout_seconds=timeout_seconds,
                allow_background_timeout=True,
                max_output_tokens=320,
                model_id=model_id,
            ),
            category="maintenance_conversation_compaction",
            cancellable_callable=lambda cancellation_event: qwen_chat(
                system_prompt,
                source_json,
                timeout_seconds=timeout_seconds,
                allow_background_timeout=True,
                max_output_tokens=320,
                model_id=model_id,
                cancellation_event=cancellation_event,
            ),
        )
        raw = admission.value
    except (QwenClientError, ModelAdmissionError, TimeoutError) as exc:
        return _deterministic_summary(
            existing_summary=existing_summary,
            source_rows=source_rows,
            reason=f"model_error:{type(exc).__name__}",
        )
    payload = _json_object(raw)
    allowed_numbers = _numeric_tokens(grounding_source)
    allowed_codes = {
        str(row.get("stock_code") or "")
        for row in source_rows
        if re.fullmatch(r"\d{4}", str(row.get("stock_code") or ""))
    }
    validated, validation_reason = _validate_summary_payload(
        payload,
        allowed_numbers=allowed_numbers,
        allowed_stock_codes=allowed_codes,
    )
    if validated is None:
        return _deterministic_summary(
            existing_summary=existing_summary,
            source_rows=source_rows,
            reason=f"summary_validation_failed:{validation_reason or 'invalid_payload'}",
        )
    summary, stock_summaries = validated
    return ConversationCompactionResult(True, summary, stock_summaries, model_id)
