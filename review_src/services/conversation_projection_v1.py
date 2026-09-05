from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping


CONVERSATION_PROJECTION_VERSION = "ConversationProjectionV1"
MAX_RECENT_TURNS = 12
MAX_PROJECTION_TOKENS = 4000
ROLLING_SUMMARY_VERSION = "line-memory-summary-v1"
_NUMBER_OR_DATE = re.compile(
    r"(?<![A-Za-z])(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}|[-+]?\d+(?:[.,]\d+)?%?)"
)


def _clean(value: Any, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum]


def conservative_token_upper_bound(value: Any) -> int:
    """UTF-8 bytes are a conservative upper bound for common BPE token counts."""

    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return len(serialized.encode("utf-8"))


def _entity(value: Any) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    code = str(value.get("code") or value.get("stock_code") or "")
    if not re.fullmatch(r"\d{4}", code):
        return None
    return {
        "code": code,
        "name": _clean(value.get("name") or value.get("trading_name"), 80),
    }


def _deduplicate_entities(values: Iterable[Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        entity = _entity(value)
        if entity is None or entity["code"] in seen:
            continue
        seen.add(entity["code"])
        result.append(entity)
    return result


def _explicit_profile(session: Mapping[str, Any]) -> dict[str, str | None]:
    position = str(session.get("position_state") or "").strip()
    horizon = str(session.get("investment_horizon") or "").strip()
    allowed_positions = {"持有", "空手", "想加碼", "想減碼", ""}
    return {
        "position_state": position if position in allowed_positions and position else None,
        "investment_horizon": _clean(horizon, 80) or None,
    }


def _historical_claims(turns: list[dict[str, str]]) -> list[dict[str, Any]]:
    claims = []
    for index, turn in enumerate(turns):
        tokens = [match.group(0) for match in _NUMBER_OR_DATE.finditer(turn["text"])]
        if tokens:
            claims.append(
                {
                    "turn_index": index,
                    "role": turn["role"],
                    "tokens": tokens[:20],
                    "claim_status": "historical_conversation_claim",
                    "can_replace_canonical_fact": False,
                }
            )
    return claims


def build_conversation_projection_v1(
    session: Mapping[str, Any] | None,
    *,
    recent_exchanges: Iterable[Mapping[str, Any]] | None = None,
    rolling_summary: Mapping[str, Any] | None = None,
    resolved_entities: Iterable[Mapping[str, Any]] | None = None,
    comparison_entities: Iterable[Mapping[str, Any]] | None = None,
    last_analysis_id: str | None = None,
    last_analysis_cutoff: str | None = None,
) -> dict[str, Any]:
    """Build a bounded, non-authoritative projection for Web and LINE."""

    state = dict(session or {})
    active = _entity(state.get("active_stock"))
    if active is None and re.fullmatch(r"\d{4}", str(state.get("code") or "")):
        active = {"code": str(state["code"]), "name": _clean(state.get("stock_name"), 80)}
    resolved = _deduplicate_entities(resolved_entities or [])
    comparisons = _deduplicate_entities(comparison_entities or [])
    if resolved:
        active = resolved[0]
    referenced = _deduplicate_entities(
        [*(state.get("referenced_stocks") or []), *resolved, *comparisons]
    )

    exchanges = list(recent_exchanges or [])[-6:]
    turns: list[dict[str, str]] = []
    for exchange in exchanges:
        user = _clean(exchange.get("user"), 350)
        assistant = _clean(exchange.get("assistant"), 450)
        if user:
            turns.append({"role": "user", "text": user})
        if assistant:
            turns.append({"role": "assistant", "text": assistant})
    turns = turns[-MAX_RECENT_TURNS:]
    summary = {
        "version": str((rolling_summary or {}).get("summary_version") or ROLLING_SUMMARY_VERSION),
        "text": _clean((rolling_summary or {}).get("summary"), 400),
        "unresolved_questions": [
            _clean(value, 300)
            for value in list((rolling_summary or {}).get("unresolved_questions") or [])[:8]
            if str(value).strip()
        ],
    }
    profile = _explicit_profile(state)
    projection: dict[str, Any] = {
        "projection_version": CONVERSATION_PROJECTION_VERSION,
        "recent_turns": turns,
        "rolling_summary": summary,
        "active_stock": active,
        "referenced_stocks": referenced,
        "comparison_stocks": comparisons,
        "last_intent": _clean(state.get("last_intent") or state.get("last_mode"), 80) or None,
        "last_topic": _clean(state.get("last_topic") or state.get("last_focus"), 80) or None,
        "unresolved_question": _clean(state.get("unresolved_question"), 300) or None,
        **profile,
        "last_analysis_id": _clean(last_analysis_id or state.get("last_analysis_id"), 160) or None,
        "last_analysis_cutoff": _clean(last_analysis_cutoff or state.get("last_analysis_cutoff"), 48) or None,
        "historical_conversation_claims": _historical_claims(turns),
        "market_fact_authority": "historical_context_only",
        "can_replace_canonical_facts": False,
    }
    while len(turns) > 2 and conservative_token_upper_bound(projection) > 3700:
        turns.pop(0)
        projection["historical_conversation_claims"] = _historical_claims(turns)
    if conservative_token_upper_bound(projection) > 3700:
        projection["rolling_summary"]["text"] = projection["rolling_summary"]["text"][:120]
        projection["rolling_summary"]["unresolved_questions"] = []
    if conservative_token_upper_bound(projection) > 3700:
        for turn in turns:
            turn["text"] = turn["text"][:160]
        projection["historical_conversation_claims"] = _historical_claims(turns)
    token_count = conservative_token_upper_bound(projection)
    projection["turn_count"] = len(turns)
    projection["token_count_upper_bound"] = token_count
    digest_source = dict(projection)
    projection["projection_digest"] = hashlib.sha256(
        json.dumps(digest_source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return projection
