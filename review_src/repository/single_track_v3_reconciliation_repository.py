from __future__ import annotations

"""Persistence helpers for research-to-event reconciliation.

These helpers never decide source authority or event meaning.  The task layer
must supply a validated cluster/revision identity and owns the transaction.
"""

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any


def _json_load(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _aware(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset")
    return parsed


def event_cluster_by_dedup_key(
    conn: sqlite3.Connection,
    dedup_key: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM event_cluster WHERE dedup_key=?",
        (str(dedup_key),),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["entity_refs"] = _json_load(result.pop("entity_refs_json"), [])
    return result


def latest_event_revision_for_cluster(
    conn: sqlite3.Connection,
    event_cluster_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM event_revision
        WHERE event_cluster_id=?
        ORDER BY revision_no DESC,event_revision_id DESC LIMIT 1
        """,
        (str(event_cluster_id),),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    for field, default in (
        ("key_points_json", []),
        ("source_refs_json", []),
        ("price_reaction_json", {}),
    ):
        result[field.removesuffix("_json")] = _json_load(result.pop(field), default)
    return result


def extend_event_cluster_observation(
    conn: sqlite3.Connection,
    *,
    event_cluster_id: str,
    entity_refs: Sequence[str],
    last_seen_at: str,
    updated_at: str,
) -> dict[str, Any]:
    _aware(last_seen_at, "last_seen_at")
    _aware(updated_at, "updated_at")
    current = conn.execute(
        "SELECT * FROM event_cluster WHERE event_cluster_id=?",
        (str(event_cluster_id),),
    ).fetchone()
    if current is None:
        raise ValueError("event cluster does not exist")
    row = dict(current)
    existing_refs = set(_json_load(row["entity_refs_json"], []))
    requested_refs = {str(value) for value in entity_refs if str(value)}
    if existing_refs != requested_refs:
        raise ValueError("event cluster entity identity cannot change")
    if _aware(last_seen_at, "last_seen_at") < _aware(row["last_seen_at"], "existing.last_seen_at"):
        raise ValueError("event cluster last_seen_at cannot move backwards")
    conn.execute(
        """
        UPDATE event_cluster SET last_seen_at=?,updated_at=?
        WHERE event_cluster_id=?
        """,
        (last_seen_at, updated_at, str(event_cluster_id)),
    )
    saved = conn.execute(
        "SELECT * FROM event_cluster WHERE event_cluster_id=?",
        (str(event_cluster_id),),
    ).fetchone()
    if saved is None:
        raise RuntimeError("event cluster could not be read back")
    result = dict(saved)
    result["entity_refs"] = _json_load(result.pop("entity_refs_json"), [])
    return result


def link_research_items_to_event_revision(
    conn: sqlite3.Connection,
    *,
    news_item_ids: Sequence[str],
    event_cluster_id: str,
    event_revision_id: str,
    updated_at: str,
) -> int:
    _aware(updated_at, "updated_at")
    ids = sorted({str(value) for value in news_item_ids if str(value)})
    if not ids:
        raise ValueError("news_item_ids must not be empty")
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT news_item_id,event_cluster_id,event_revision_id
        FROM research_news_item WHERE news_item_id IN ({placeholders})
        """,
        tuple(ids),
    ).fetchall()
    if {str(row["news_item_id"]) for row in rows} != set(ids):
        raise ValueError("all research news items must exist")
    for row in rows:
        existing_pair = (row["event_cluster_id"], row["event_revision_id"])
        if existing_pair not in {
            (None, None),
            (str(event_cluster_id), str(event_revision_id)),
        }:
            raise ValueError("research news item already belongs to another event revision")
    before = conn.total_changes
    conn.execute(
        f"""
        UPDATE research_news_item
        SET event_cluster_id=?,event_revision_id=?,updated_at=?
        WHERE news_item_id IN ({placeholders})
          AND event_cluster_id IS NULL AND event_revision_id IS NULL
        """,
        (str(event_cluster_id), str(event_revision_id), updated_at, *ids),
    )
    return conn.total_changes - before
