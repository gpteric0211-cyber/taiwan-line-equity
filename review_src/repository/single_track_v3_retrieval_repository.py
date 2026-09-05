from __future__ import annotations

"""Durable, noncanonical receipts for the Single-Track V3 retrieval worker."""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from core.single_track_v3_schema import ensure_single_track_v3_schema


_OUTCOMES = {"success", "no_results", "failed"}
_FAILURE_CLASSES = {
    "none",
    "timeout",
    "rate_limited",
    "offline",
    "source_error",
    "policy_disabled",
    "invalid_response",
}
_TERMINAL_STATUSES = {"success", "partial", "failed", "late"}
_EVENT_ACTIONS = {"discovered", "updated", "unchanged"}


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("retrieval receipt must be finite JSON") from exc


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


def _digest(value: Any, field: str) -> str:
    text = _required_text(value, field).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return text


def _select_one(
    conn: sqlite3.Connection,
    sql: str,
    parameters: tuple[Any, ...],
) -> dict[str, Any] | None:
    cursor = conn.execute(sql, parameters)
    row = cursor.fetchone()
    if row is None:
        return None
    columns = [str(column[0]) for column in cursor.description or ()]
    return dict(zip(columns, row))


def _bounded_unique_strings(
    values: Iterable[Any],
    field: str,
    *,
    maximum_items: int = 512,
) -> list[str]:
    result = [_required_text(value, field) for value in values]
    if len(result) > maximum_items:
        raise ValueError(f"{field} exceeds its item limit")
    if len(result) != len(set(result)):
        raise ValueError(f"{field} must not contain duplicates")
    return sorted(result)


def retrieval_source_attempts(
    conn: sqlite3.Connection,
    run_id: str,
) -> list[dict[str, Any]]:
    """Read persisted source outcomes without schema mutation."""

    cursor = conn.execute(
        """
        SELECT * FROM single_track_v3_retrieval_source_attempt
        WHERE run_id=?
        ORDER BY source_id,query_digest,attempt_ordinal,attempt_id
        """,
        (str(run_id),),
    )
    columns = [str(column[0]) for column in cursor.description or ()]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def record_retrieval_source_attempt(
    conn: sqlite3.Connection,
    item: Mapping[str, Any],
    *,
    ensure_schema: bool = True,
) -> dict[str, Any]:
    """Seal one metadata-only source outcome; response bodies are never accepted."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    row = dict(item)
    for field in (
        "attempt_id",
        "run_id",
        "source_id",
        "scope_key",
        "adapter_version",
        "source_policy_version",
    ):
        row[field] = _required_text(row.get(field), field)
    row["query_digest"] = _digest(row.get("query_digest"), "query_digest")
    row["result_digest"] = _digest(row.get("result_digest"), "result_digest")
    row["attempt_ordinal"] = int(row.get("attempt_ordinal") or 0)
    if row["attempt_ordinal"] < 1:
        raise ValueError("attempt_ordinal must be at least 1")
    row["outcome"] = _required_text(row.get("outcome"), "outcome")
    if row["outcome"] not in _OUTCOMES:
        raise ValueError("outcome is invalid")
    row["failure_class"] = _required_text(
        row.get("failure_class"), "failure_class"
    )
    if row["failure_class"] not in _FAILURE_CLASSES:
        raise ValueError("failure_class is invalid")
    if (row["outcome"] == "failed") != (row["failure_class"] != "none"):
        raise ValueError("failed outcomes require a failure class and successful outcomes require none")
    row["source_status"] = _required_text(row.get("source_status"), "source_status")
    if len(row["source_status"]) > 64:
        raise ValueError("source_status exceeds 64 characters")
    row["item_count"] = int(row.get("item_count") or 0)
    if row["item_count"] < 0:
        raise ValueError("item_count cannot be negative")
    started = _aware_timestamp(row.get("started_at"), "started_at")
    completed = _aware_timestamp(row.get("completed_at"), "completed_at")
    _aware_timestamp(row.get("created_at"), "created_at")
    if completed < started:
        raise ValueError("completed_at cannot precede started_at")
    for field in (
        "raw_article_bodies_fetched",
        "raw_body_retention_seconds",
        "canonical_table_writes",
    ):
        row[field] = int(row.get(field) or 0)
        if row[field] != 0:
            raise ValueError(f"{field} must remain zero")
    columns = (
        "attempt_id",
        "run_id",
        "query_digest",
        "source_id",
        "scope_key",
        "attempt_ordinal",
        "outcome",
        "source_status",
        "failure_class",
        "item_count",
        "adapter_version",
        "source_policy_version",
        "started_at",
        "completed_at",
        "result_digest",
        "raw_article_bodies_fetched",
        "raw_body_retention_seconds",
        "canonical_table_writes",
        "created_at",
    )
    values = {column: row.get(column) for column in columns}
    try:
        conn.execute(
            f"INSERT INTO single_track_v3_retrieval_source_attempt({','.join(columns)}) "
            f"VALUES({','.join(':' + column for column in columns)}) "
            "ON CONFLICT(attempt_id) DO NOTHING",
            values,
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("retrieval source attempt identity conflicts with existing content") from exc
    saved = _select_one(
        conn,
        "SELECT * FROM single_track_v3_retrieval_source_attempt WHERE attempt_id=?",
        (row["attempt_id"],),
    )
    if saved is None:
        raise RuntimeError("retrieval source attempt could not be read back")
    if any(saved.get(column) != values.get(column) for column in columns):
        raise ValueError("retrieval source attempt already exists with different content")
    return saved


def link_research_run_item(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    news_item_id: str,
    event_action: str,
    linked_at: str,
    ensure_schema: bool = True,
) -> dict[str, Any]:
    """Link a run to noncanonical research metadata without canonical promotion."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    values = {
        "run_id": _required_text(run_id, "run_id"),
        "news_item_id": _required_text(news_item_id, "news_item_id"),
        "event_action": _required_text(event_action, "event_action"),
        "linked_at": _required_text(linked_at, "linked_at"),
    }
    if values["event_action"] not in _EVENT_ACTIONS:
        raise ValueError("event_action is invalid")
    _aware_timestamp(values["linked_at"], "linked_at")
    try:
        conn.execute(
            """
            INSERT INTO single_track_v3_research_run_item(
                run_id,news_item_id,event_action,linked_at
            ) VALUES(:run_id,:news_item_id,:event_action,:linked_at)
            ON CONFLICT(run_id,news_item_id) DO NOTHING
            """,
            values,
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("research run/item identity conflicts with existing content") from exc
    saved = _select_one(
        conn,
        """
        SELECT * FROM single_track_v3_research_run_item
        WHERE run_id=? AND news_item_id=?
        """,
        (values["run_id"], values["news_item_id"]),
    )
    if saved is None:
        raise RuntimeError("research run/item link could not be read back")
    if any(saved.get(column) != value for column, value in values.items()):
        raise ValueError("research run/item link already exists with different content")
    return saved


def retrieval_worker_receipt(
    conn: sqlite3.Connection,
    run_id: str,
) -> dict[str, Any] | None:
    """Read an immutable terminal worker receipt without schema mutation."""

    row = _select_one(
        conn,
        "SELECT * FROM single_track_v3_retrieval_worker_receipt WHERE run_id=?",
        (str(run_id),),
    )
    if row is None:
        return None
    row["source_attempt_ids"] = _json_load(row.pop("source_attempt_ids_json"), [])
    row["news_item_ids"] = _json_load(row.pop("news_item_ids_json"), [])
    row["entity_refs"] = _json_load(row.pop("entity_refs_json"), [])
    row["query_digests"] = _json_load(row.pop("query_digests_json"), [])
    return row


def seal_retrieval_worker_receipt(
    conn: sqlite3.Connection,
    item: Mapping[str, Any],
    *,
    ensure_schema: bool = True,
) -> dict[str, Any]:
    """Seal the exactly-once terminal receipt in the run's terminal transaction."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    row = dict(item)
    for field in (
        "receipt_id",
        "run_id",
        "terminal_outbox_id",
        "worker_contract_version",
        "query_plan_version",
        "source_plan_version",
    ):
        row[field] = _required_text(row.get(field), field)
    row["terminal_status"] = _required_text(
        row.get("terminal_status"), "terminal_status"
    )
    if row["terminal_status"] not in _TERMINAL_STATUSES:
        raise ValueError("terminal_status is invalid")
    row["result_digest"] = _digest(row.get("result_digest"), "result_digest")
    row["source_attempt_ids_json"] = _canonical_json(
        _bounded_unique_strings(row.get("source_attempt_ids") or [], "source_attempt_ids")
    )
    row["news_item_ids_json"] = _canonical_json(
        _bounded_unique_strings(row.get("news_item_ids") or [], "news_item_ids")
    )
    row["entity_refs_json"] = _canonical_json(
        _bounded_unique_strings(
            row.get("entity_refs") or [],
            "entity_refs",
            maximum_items=32,
        )
    )
    query_digests = _bounded_unique_strings(
        row.get("query_digests") or [],
        "query_digests",
        maximum_items=24,
    )
    for value in query_digests:
        _digest(value, "query_digests")
    row["query_digests_json"] = _canonical_json(query_digests)
    row["pruned_item_count"] = int(row.get("pruned_item_count") or 0)
    if row["pruned_item_count"] < 0:
        raise ValueError("pruned_item_count cannot be negative")
    for field in (
        "zero_model_calls",
        "raw_article_bodies_retained",
        "canonical_table_writes",
    ):
        row[field] = int(row.get(field) or 0)
        if row[field] != 0:
            raise ValueError(f"{field} must remain zero")
    _aware_timestamp(row.get("completed_at"), "completed_at")
    _aware_timestamp(row.get("created_at"), "created_at")
    run = _select_one(
        conn,
        "SELECT status,completed_at FROM news_retrieval_run WHERE run_id=?",
        (row["run_id"],),
    )
    if run is None or run.get("status") != row["terminal_status"]:
        raise ValueError("retrieval receipt must match a terminal retrieval run")
    if run.get("completed_at") != row.get("completed_at"):
        raise ValueError("retrieval receipt completed_at must match its terminal run")
    columns = (
        "receipt_id",
        "run_id",
        "terminal_status",
        "source_attempt_ids_json",
        "news_item_ids_json",
        "pruned_item_count",
        "terminal_outbox_id",
        "worker_contract_version",
        "entity_refs_json",
        "query_digests_json",
        "query_plan_version",
        "source_plan_version",
        "result_digest",
        "zero_model_calls",
        "raw_article_bodies_retained",
        "canonical_table_writes",
        "completed_at",
        "created_at",
    )
    values = {column: row.get(column) for column in columns}
    try:
        conn.execute(
            f"INSERT INTO single_track_v3_retrieval_worker_receipt({','.join(columns)}) "
            f"VALUES({','.join(':' + column for column in columns)}) "
            "ON CONFLICT(run_id) DO NOTHING",
            values,
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("retrieval worker receipt identity conflicts with existing content") from exc
    saved = _select_one(
        conn,
        "SELECT * FROM single_track_v3_retrieval_worker_receipt WHERE run_id=?",
        (row["run_id"],),
    )
    if saved is None:
        raise RuntimeError("retrieval worker receipt could not be read back")
    if any(saved.get(column) != values.get(column) for column in columns):
        raise ValueError("retrieval worker receipt already exists with different content")
    decoded = retrieval_worker_receipt(conn, row["run_id"])
    if decoded is None:
        raise RuntimeError("retrieval worker receipt could not be decoded")
    return decoded
