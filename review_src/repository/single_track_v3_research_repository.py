from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from core.single_track_v3_schema import ensure_single_track_v3_schema


_SOURCE_CLASSES = {
    "canonical_official",
    "canonical_normalized_supplemental",
    "licensed_secondary",
    "news_radar",
    "community_claim",
}
_VERIFICATION_STATES = {
    "discovered",
    "unverified",
    "primary_verified",
    "secondary_corroborated",
    "contradicted",
    "pending_reconciliation",
    "expired",
}
_RETENTION_CLASSES = {"hot_news", "canonical_disclosure", "community_claim"}
_FORBIDDEN_CONTENT_KEYS = {
    "raw_body",
    "article_body",
    "full_article_body",
    "full_text",
    "html_body",
    "ocr_text",
    "member_name",
    "profile_name",
    "username",
    "user_id",
}
_RIGHTS_BOOLEAN_FIELDS = (
    "allow_fetch",
    "allow_model",
    "allow_display",
    "allow_store_excerpt",
)
TPE = ZoneInfo("Asia/Taipei")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _aware_timestamp(value: Any, field: str) -> datetime:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def _sha256_digest(value: Any, field: str) -> str:
    text = _required_text(value, field).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return text


def _contains_forbidden_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_CONTENT_KEYS:
                return normalized
            found = _contains_forbidden_key(nested)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _contains_forbidden_key(nested)
            if found is not None:
                return found
    return None


def _string_list(
    value: Any,
    field: str,
    *,
    maximum_items: int,
    maximum_length: int,
) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    normalized = [_required_text(item, field) for item in value]
    if len(normalized) > maximum_items:
        raise ValueError(f"{field} exceeds its item limit")
    if any(len(item) > maximum_length for item in normalized):
        raise ValueError(f"{field} contains an overlong item")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field} must not contain duplicates")
    return normalized


def _source_rights(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("source_rights must be an object")
    rights = dict(value)
    for field in _RIGHTS_BOOLEAN_FIELDS:
        if not isinstance(rights.get(field), bool):
            raise ValueError(f"source_rights.{field} must be a boolean")
    retention_days = rights.get("content_retention_days")
    if isinstance(retention_days, bool) or not isinstance(retention_days, int):
        raise ValueError("source_rights.content_retention_days must be an integer")
    if retention_days < 0 or retention_days > 7:
        raise ValueError("source_rights.content_retention_days must be between 0 and 7")
    rights["attribution"] = _required_text(rights.get("attribution"), "source_rights.attribution")
    return rights


def _decode_news_item(source: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(source)
    row["key_points"] = _json_load(row.pop("key_points_json"), [])
    row["entity_refs"] = _json_load(row.pop("entity_refs_json"), [])
    row["source_rights"] = _json_load(row.pop("source_rights_json"), {})
    row["publisher_time_verified"] = bool(row["publisher_time_verified"])
    row["untrusted_text"] = bool(row["untrusted_text"])
    row["raw_body_retained"] = bool(row["raw_body_retained"])
    return row


def research_news_item(
    conn: sqlite3.Connection,
    news_item_id: str,
) -> dict[str, Any] | None:
    """Read one research item without performing schema DDL or repair work."""

    source = conn.execute(
        "SELECT * FROM research_news_item WHERE news_item_id=?",
        (str(news_item_id),),
    ).fetchone()
    return _decode_news_item(dict(source)) if source is not None else None


def record_research_news_item(
    conn: sqlite3.Connection,
    item: Mapping[str, Any],
    *,
    ensure_schema: bool = True,
) -> dict[str, Any]:
    """Persist bounded noncanonical research metadata with immutable PIT timestamps."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    forbidden = _contains_forbidden_key(item)
    if forbidden is not None:
        raise ValueError(f"research news item contains forbidden field: {forbidden}")
    row = dict(item)
    news_item_id = _required_text(row.get("news_item_id"), "news_item_id")
    source_id = _required_text(row.get("source_id"), "source_id")
    source_class = _required_text(row.get("source_class"), "source_class")
    if source_class not in _SOURCE_CLASSES:
        raise ValueError("source_class is invalid")
    retention_class = _required_text(row.get("retention_class"), "retention_class")
    if retention_class not in _RETENTION_CLASSES:
        raise ValueError("retention_class is invalid")
    if (source_class == "community_claim") != (retention_class == "community_claim"):
        raise ValueError("community claims require the community_claim retention class")
    verification_state = _required_text(
        row.get("verification_state"), "verification_state"
    )
    if verification_state not in _VERIFICATION_STATES:
        raise ValueError("verification_state is invalid")

    source_url = _required_text(row.get("source_url"), "source_url")
    parsed_url = urlsplit(source_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise ValueError("source_url must be an absolute HTTP(S) URL")
    title = str(row.get("title") or "").strip()
    if len(title) > 300:
        raise ValueError("title exceeds 300 characters")
    short_excerpt = str(row.get("short_excerpt") or "").strip()
    if len(short_excerpt) > 600:
        raise ValueError("short_excerpt exceeds 600 characters")
    key_points = _string_list(
        row.get("key_points", []),
        "key_points",
        maximum_items=12,
        maximum_length=320,
    )
    entity_refs = _string_list(
        row.get("entity_refs", []),
        "entity_refs",
        maximum_items=32,
        maximum_length=128,
    )
    rights = _source_rights(row.get("source_rights"))
    if short_excerpt and not rights["allow_store_excerpt"]:
        raise ValueError("source rights do not allow storing an excerpt")
    if row.get("untrusted_text") is not True:
        raise ValueError("research news text must be marked untrusted_text=true")
    if row.get("raw_body_retained") not in (None, False, 0):
        raise ValueError("raw article body retention must remain zero")

    first_retrieved_at = _aware_timestamp(
        row.get("first_retrieved_at"), "first_retrieved_at"
    )
    last_retrieved_at = _aware_timestamp(
        row.get("last_retrieved_at"), "last_retrieved_at"
    )
    available_at = _aware_timestamp(row.get("available_at"), "available_at")
    content_expires_at = _aware_timestamp(
        row.get("content_expires_at"), "content_expires_at"
    )
    created_at = _aware_timestamp(row.get("created_at"), "created_at")
    updated_at = _aware_timestamp(row.get("updated_at"), "updated_at")
    if last_retrieved_at < first_retrieved_at:
        raise ValueError("last_retrieved_at cannot precede first_retrieved_at")
    if available_at > first_retrieved_at:
        raise ValueError("available_at cannot be later than first_retrieved_at")
    if updated_at < created_at:
        raise ValueError("updated_at cannot precede created_at")
    maximum_expiry = first_retrieved_at + timedelta(
        days=min(7, int(rights["content_retention_days"]))
    )
    if content_expires_at < first_retrieved_at or content_expires_at > maximum_expiry:
        raise ValueError("content_expires_at exceeds the source-rights retention limit")
    for optional_timestamp in ("publisher_published_at", "index_seen_at"):
        if row.get(optional_timestamp) is not None:
            _aware_timestamp(row[optional_timestamp], optional_timestamp)
    publisher_published_date = None
    if row.get("publisher_published_date") is not None:
        try:
            publisher_published_date = date.fromisoformat(
                _required_text(
                    row.get("publisher_published_date"),
                    "publisher_published_date",
                )
            ).isoformat()
        except ValueError as exc:
            raise ValueError("publisher_published_date must be an ISO-8601 date") from exc
        if publisher_published_date > first_retrieved_at.astimezone(TPE).date().isoformat():
            raise ValueError("publisher_published_date cannot be after first retrieval")
        if row.get("publisher_published_at") is not None:
            exact_date = _aware_timestamp(
                row["publisher_published_at"], "publisher_published_at"
            ).astimezone(TPE).date().isoformat()
            if publisher_published_date != exact_date:
                raise ValueError(
                    "publisher_published_date conflicts with exact publisher timestamp"
                )
    publisher_time_verified = row.get("publisher_time_verified", False)
    if not isinstance(publisher_time_verified, bool):
        raise ValueError("publisher_time_verified must be a boolean")
    if (
        source_id.casefold().startswith("gdelt")
        and row.get("publisher_published_at") is not None
        and not publisher_time_verified
    ):
        raise ValueError("GDELT publisher time requires separate verification")

    content_hash = _sha256_digest(row.get("content_hash"), "content_hash")
    event_fingerprint = _sha256_digest(
        row.get("event_fingerprint"), "event_fingerprint"
    )
    first_run_id = _required_text(row.get("first_run_id"), "first_run_id")
    last_run_id = _required_text(row.get("last_run_id"), "last_run_id")
    known_runs = {
        str(source[0])
        for source in conn.execute(
            "SELECT run_id FROM news_retrieval_run WHERE run_id IN (?,?)",
            (first_run_id, last_run_id),
        ).fetchall()
    }
    if known_runs != {first_run_id, last_run_id}:
        raise ValueError("first_run_id and last_run_id must reference retrieval runs")

    duplicate = conn.execute(
        "SELECT news_item_id FROM research_news_item WHERE source_id=? AND content_hash=?",
        (source_id, content_hash),
    ).fetchone()
    if duplicate is not None and str(duplicate[0]) != news_item_id:
        raise ValueError("source/content identity already belongs to another news_item_id")
    existing = research_news_item(conn, news_item_id)
    if existing is not None:
        immutable_fields = {
            "source_id": source_id,
            "content_hash": content_hash,
            "first_retrieved_at": str(row.get("first_retrieved_at")),
            "first_run_id": first_run_id,
            "created_at": str(row.get("created_at")),
        }
        if any(existing.get(field) != value for field, value in immutable_fields.items()):
            raise ValueError("research news immutable identity fields cannot change")
        if available_at > _aware_timestamp(existing["available_at"], "existing.available_at"):
            raise ValueError("available_at cannot move later after creation")
        if content_expires_at > _aware_timestamp(
            existing["content_expires_at"], "existing.content_expires_at"
        ):
            raise ValueError("content_expires_at cannot be extended after creation")
        if last_retrieved_at < _aware_timestamp(
            existing["last_retrieved_at"], "existing.last_retrieved_at"
        ):
            raise ValueError("last_retrieved_at cannot move backwards")
        if existing.get("content_pruned_at") and (title or key_points or short_excerpt):
            raise ValueError("pruned research content cannot be restored")

    values = {
        "news_item_id": news_item_id,
        "source_id": source_id,
        "source_class": source_class,
        "publisher": _required_text(row.get("publisher"), "publisher"),
        "source_url": source_url,
        "publisher_published_at": row.get("publisher_published_at"),
        "publisher_published_date": publisher_published_date,
        "publisher_time_verified": int(publisher_time_verified),
        "index_seen_at": row.get("index_seen_at"),
        "first_retrieved_at": row.get("first_retrieved_at"),
        "last_retrieved_at": row.get("last_retrieved_at"),
        "available_at": row.get("available_at"),
        "effective_tw_trade_date": row.get("effective_tw_trade_date"),
        "verification_state": verification_state,
        "title": title,
        "key_points_json": _canonical_json(key_points),
        "short_excerpt": short_excerpt,
        "content_hash": content_hash,
        "event_fingerprint": event_fingerprint,
        "dedup_cluster": _required_text(row.get("dedup_cluster"), "dedup_cluster"),
        "entity_refs_json": _canonical_json(entity_refs),
        "event_cluster_id": row.get("event_cluster_id"),
        "event_revision_id": row.get("event_revision_id"),
        "source_rights_json": _canonical_json(rights),
        "source_policy_version": _required_text(
            row.get("source_policy_version"), "source_policy_version"
        ),
        "retention_class": retention_class,
        "content_expires_at": row.get("content_expires_at"),
        "content_pruned_at": existing.get("content_pruned_at") if existing else None,
        "untrusted_text": 1,
        "raw_body_retained": 0,
        "first_run_id": first_run_id,
        "last_run_id": last_run_id,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    columns = tuple(values)
    if existing is None:
        conn.execute(
            f"INSERT INTO research_news_item({','.join(columns)}) "
            f"VALUES({','.join(':' + column for column in columns)})",
            values,
        )
    else:
        mutable = tuple(
            column
            for column in columns
            if column
            not in {
                "news_item_id",
                "source_id",
                "content_hash",
                "first_retrieved_at",
                "first_run_id",
                "created_at",
            }
        )
        conn.execute(
            "UPDATE research_news_item SET "
            + ",".join(f"{column}=:{column}" for column in mutable)
            + " WHERE news_item_id=:news_item_id",
            values,
        )
    saved = research_news_item(conn, news_item_id)
    if saved is None:
        raise RuntimeError("research news item could not be read back")
    return saved


def research_news_items_available_at(
    conn: sqlite3.Connection,
    analysis_cutoff: str,
    *,
    verification_states: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Read bounded, unpruned research items available at a point-in-time cutoff."""

    cutoff = _aware_timestamp(analysis_cutoff, "analysis_cutoff")
    requested_states = set(verification_states or _VERIFICATION_STATES)
    if not requested_states <= _VERIFICATION_STATES:
        raise ValueError("verification_states contains an invalid value")
    rows = conn.execute(
        "SELECT * FROM research_news_item WHERE content_pruned_at IS NULL ORDER BY available_at,news_item_id"
    ).fetchall()
    result = []
    for source in rows:
        row = _decode_news_item(dict(source))
        if row["verification_state"] not in requested_states:
            continue
        if _aware_timestamp(row["available_at"], "item.available_at") > cutoff:
            continue
        if _aware_timestamp(row["content_expires_at"], "item.content_expires_at") < cutoff:
            continue
        result.append(row)
    return result


def prune_expired_research_content(
    conn: sqlite3.Connection,
    *,
    pruned_at: str,
    ensure_schema: bool = True,
) -> int:
    """Remove bounded hot content while retaining URL/hash/event audit metadata."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    _aware_timestamp(pruned_at, "pruned_at")
    before = conn.total_changes
    conn.execute(
        """
        UPDATE research_news_item SET
            title='',key_points_json='[]',short_excerpt='',
            content_pruned_at=?,verification_state='expired',updated_at=?
        WHERE content_pruned_at IS NULL
          AND julianday(content_expires_at)<=julianday(?)
        """,
        (pruned_at, pruned_at, pruned_at),
    )
    return conn.total_changes - before
