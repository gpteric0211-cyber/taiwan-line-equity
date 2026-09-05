from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from html import unescape
from typing import Any, Iterable, Mapping

from repository.market_microstructure_repository import read_active_stock_master_rows


STOCK_ENTITY_REGISTRY_VERSION = "StockEntityRegistryV1"
REVIEWED_ALIASES: tuple[dict[str, Any], ...] = (
    {
        "alias": "星宇",
        "stock_code": "2646",
        "source": "reviewed_common_alias",
        "confidence": 1.0,
        "effective_from": "2023-04-21",
    },
    {
        "alias": "台積",
        "stock_code": "2330",
        "source": "reviewed_common_alias",
        "confidence": 1.0,
        "effective_from": "1994-09-05",
    },
)
_LEAD_INS = (
    "那所以想問",
    "所以想問",
    "我想問",
    "那所以",
    "那請問",
    "接著看",
    "再來看",
    "換成",
    "改看",
    "所以",
    "至於",
    "另外",
    "再來",
    "接著",
    "想問",
    "請問",
    "那麼",
    "那",
)
_TRAILING_NOISE = (
    "今天收盤價",
    "今天怎麼樣",
    "現在呢",
    "明天呢",
    "怎麼樣",
    "怎麼看",
    "分析一下",
    "分析",
    "呢",
    "嗎",
)
_CONTEXT_REFERENCES = (
    "它",
    "他",
    "她",
    "那檔",
    "這檔",
    "該股",
    "上一檔",
    "剛才那檔",
    "那現在",
    "接下來",
)
_COMPARISON_CUES = ("比較", "對比", "和", "與", "跟", "vs", "VS")


def normalize_entity_text(value: Any) -> str:
    return re.sub(
        r"[\s，。！？、,.!?：:；;（）()【】\[\]「」『』]+",
        "",
        unicodedata.normalize("NFKC", unescape(str(value or ""))).casefold(),
    )


def _without_legal_suffix(name: str) -> str:
    value = unescape(str(name or "")).strip()
    for suffix in ("股份有限公司", "有限公司", "公司"):
        if value.endswith(suffix):
            return value[: -len(suffix)].strip()
    return value


def stock_names(row: Mapping[str, Any]) -> list[str]:
    official = unescape(str(row.get("name") or row.get("canonical_name") or "")).strip()
    trading = unescape(str(row.get("trading_name") or "")).strip()
    values = (
        re.sub(r"[*＊]", "", trading).strip(),
        _without_legal_suffix(official),
        official,
    )
    return list(dict.fromkeys(value for value in values if value))


def canonical_entity(row: Mapping[str, Any], *, resolution_source: str) -> dict[str, Any]:
    names = stock_names(row)
    official = unescape(str(row.get("name") or row.get("canonical_name") or "")).strip()
    return {
        "code": str(row.get("code") or row.get("stock_code") or "").zfill(4),
        "name": names[0] if names else official,
        "canonical_name": official,
        "trading_name": names[0] if names else official,
        "market": row.get("market"),
        "exchange": row.get("exchange"),
        "resolution_source": resolution_source,
        "registry_version": STOCK_ENTITY_REGISTRY_VERSION,
    }


def _probe(query: str) -> str:
    value = normalize_entity_text(query)
    for _ in range(2):
        prefix = next((item for item in _LEAD_INS if value.startswith(normalize_entity_text(item))), None)
        if prefix:
            value = value[len(normalize_entity_text(prefix)) :]
    for suffix in _TRAILING_NOISE:
        normalized = normalize_entity_text(suffix)
        if value.endswith(normalized):
            value = value[: -len(normalized)]
            break
    return value[:40]


def _edit_distance_at_most_one(left: str, right: str) -> bool:
    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    short, long = (left, right) if len(left) < len(right) else (right, left)
    for index in range(len(long)):
        if long[:index] + long[index + 1 :] == short:
            return True
    return False


def _ordered_unique(candidates: Iterable[tuple[int, Mapping[str, Any], str]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _position, row, source in sorted(candidates, key=lambda item: (item[0], str(item[1].get("code") or ""))):
        code = str(row.get("code") or "").zfill(4)
        if code in seen:
            continue
        seen.add(code)
        selected.append(canonical_entity(row, resolution_source=source))
    return selected


def _identity_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def resolve_stock_entity_query(
    query: str,
    *,
    active_rows: Iterable[Mapping[str, Any]],
    active_stock_code: str | None = None,
) -> dict[str, Any]:
    """Resolve only from an official active universe plus reviewed aliases."""

    text = str(query or "").strip()
    if not text or len(text) > 200:
        return {"ok": False, "status": "invalid_request", "candidates": [], "suggestions": []}
    rows = [dict(row) for row in active_rows]
    by_code = {str(row.get("code") or "").zfill(4): row for row in rows}
    normalized_text = normalize_entity_text(text)
    text_without_dates = re.sub(r"(?<!\d)20\d{2}[-/]\d{1,2}[-/]\d{1,2}(?!\d)", " ", text)
    code_matches = list(dict.fromkeys(re.findall(r"(?<!\d)(\d{4})(?!\d)", text_without_dates)))
    unknown_codes = [code for code in code_matches if code not in by_code]
    mentions: list[tuple[int, Mapping[str, Any], str]] = []
    for code in code_matches:
        if code in by_code:
            mentions.append((max(0, text.find(code)), by_code[code], "official_code"))

    name_spans: list[tuple[int, int, Mapping[str, Any]]] = []
    for row in rows:
        for name in stock_names(row):
            normalized_name = normalize_entity_text(name)
            if not normalized_name:
                continue
            for match in re.finditer(re.escape(normalized_name), normalized_text):
                name_spans.append((match.start(), match.end(), row))
    maximal_spans = [
        candidate
        for candidate in name_spans
        if not any(
            other_start <= candidate[0]
            and other_end >= candidate[1]
            and (other_end - other_start) > (candidate[1] - candidate[0])
            for other_start, other_end, _row in name_spans
        )
    ]
    mentions.extend((start, row, "official_name") for start, _end, row in maximal_spans)

    probe = _probe(text)
    for alias in REVIEWED_ALIASES:
        alias_text = normalize_entity_text(alias["alias"])
        row = by_code.get(str(alias["stock_code"]).zfill(4))
        if row is not None and (probe == alias_text or alias_text in normalized_text):
            mentions.append((normalized_text.find(alias_text), row, "reviewed_alias"))

    entities = _ordered_unique(mentions)
    code_set = set(code_matches)
    mentioned_codes = {entity["code"] for entity in entities}
    name_or_alias_codes = {
        entity["code"]
        for entity in entities
        if entity["resolution_source"] != "official_code"
    }
    valid_code_set = code_set & set(by_code)
    comparison_requested = any(cue in text for cue in _COMPARISON_CUES)
    if code_matches and name_or_alias_codes and (unknown_codes or valid_code_set != name_or_alias_codes):
        return {
            "ok": False,
            "status": "conflict",
            "reason": "stock name and code identify different active entities",
            "candidates": entities,
            "suggestions": [],
            "requires_confirmation": True,
            "registry_version": STOCK_ENTITY_REGISTRY_VERSION,
        }
    if len(entities) > 1:
        if comparison_requested:
            result = {
                "ok": True,
                "status": "comparison",
                "reason": "two or more official entities retained for comparison",
                "stock": entities[0],
                "entities": entities,
                "comparison_stocks": entities,
                "requires_confirmation": False,
                "registry_version": STOCK_ENTITY_REGISTRY_VERSION,
            }
            result["resolution_digest"] = _identity_digest(result)
            return result
        return {
            "ok": False,
            "status": "ambiguous",
            "reason": "multiple active entities matched without a comparison request",
            "candidates": entities,
            "suggestions": [],
            "requires_confirmation": True,
            "registry_version": STOCK_ENTITY_REGISTRY_VERSION,
        }
    if len(entities) == 1:
        result = {
            "ok": True,
            "status": "ok",
            "reason": "resolved from official master or reviewed alias",
            "stock": entities[0],
            "entities": entities,
            "comparison_stocks": [],
            "requires_confirmation": False,
            "registry_version": STOCK_ENTITY_REGISTRY_VERSION,
        }
        result["resolution_digest"] = _identity_digest(result)
        return result

    prefix_matches = [
        row
        for row in rows
        if len(probe) >= 2
        and any(normalize_entity_text(name).startswith(probe) for name in stock_names(row))
    ]
    unique_prefix = {str(row.get("code") or "").zfill(4): row for row in prefix_matches}
    if len(unique_prefix) == 1:
        entity = canonical_entity(next(iter(unique_prefix.values())), resolution_source="unique_active_prefix")
        result = {
            "ok": True,
            "status": "ok",
            "reason": "resolved from a unique high-confidence active-stock prefix",
            "stock": entity,
            "entities": [entity],
            "comparison_stocks": [],
            "requires_confirmation": False,
            "registry_version": STOCK_ENTITY_REGISTRY_VERSION,
        }
        result["resolution_digest"] = _identity_digest(result)
        return result

    active_code = str(active_stock_code or "").zfill(4)
    contextual = by_code.get(active_code)
    if contextual and any(reference in normalized_text for reference in _CONTEXT_REFERENCES):
        entity = canonical_entity(contextual, resolution_source="conversation_active_entity")
        result = {
            "ok": True,
            "status": "context_inherited",
            "reason": "no new entity text; inherited the sole active conversation entity",
            "stock": entity,
            "entities": [entity],
            "comparison_stocks": [],
            "requires_confirmation": False,
            "registry_version": STOCK_ENTITY_REGISTRY_VERSION,
        }
        result["resolution_digest"] = _identity_digest(result)
        return result

    suggestions = []
    if len(probe) >= 2:
        for row in rows:
            if any(_edit_distance_at_most_one(probe, normalize_entity_text(name)) for name in stock_names(row)):
                suggestions.append(canonical_entity(row, resolution_source="typo_suggestion"))
        for alias in REVIEWED_ALIASES:
            row = by_code.get(str(alias["stock_code"]).zfill(4))
            if row is not None and _edit_distance_at_most_one(
                probe,
                normalize_entity_text(alias["alias"]),
            ):
                suggestions.append(canonical_entity(row, resolution_source="typo_suggestion"))
    suggestions = _ordered_unique(
        (index, {**by_code[item["code"]]}, "typo_suggestion")
        for index, item in enumerate(suggestions)
        if item["code"] in by_code
    )
    return {
        "ok": False,
        "status": "typo_suggestion" if suggestions else "not_found",
        "reason": "a typo or low-confidence phrase requires clarification" if suggestions else "no active entity matched",
        "candidates": [],
        "suggestions": suggestions[:5],
        "requires_confirmation": bool(suggestions),
        "registry_version": STOCK_ENTITY_REGISTRY_VERSION,
    }


def resolve_stock_entity_query_from_repository(
    query: str,
    *,
    active_stock_code: str | None = None,
) -> dict[str, Any]:
    return resolve_stock_entity_query(
        query,
        active_rows=read_active_stock_master_rows(),
        active_stock_code=active_stock_code,
    )
