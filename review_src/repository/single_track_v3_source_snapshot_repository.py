from __future__ import annotations

"""Immutable point-in-time receipts for non-news market source snapshots."""

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any

from core.single_track_v3_schema import ensure_single_track_v3_schema


SOURCE_SNAPSHOT_CONTRACT_VERSION = "SingleTrackV3SourceSnapshotReceiptV1"
SOURCE_SNAPSHOT_BINDINGS = {
    "related_overseas_price_snapshot": {
        "scope_key": "related_overseas_price_reaction",
        "authority_tiers": {"canonical_normalized_supplemental"},
        "entity_scoped": True,
    },
    "us_market_snapshot": {
        "scope_key": "us_market_taiwan_night",
        "authority_tiers": {"canonical_normalized_supplemental"},
        "entity_scoped": False,
    },
    "taifex_night_snapshot": {
        "scope_key": "us_market_taiwan_night",
        "authority_tiers": {"canonical_official"},
        "entity_scoped": False,
    },
    "dilution_valuation_snapshot": {
        "scope_key": "dilution_valuation_risk",
        "authority_tiers": {
            "canonical_official",
            "canonical_normalized_supplemental",
        },
        "entity_scoped": True,
    },
}
_SNAPSHOT_STATUSES = {
    "ok",
    "partial",
    "unavailable",
    "stale",
    "source_delayed",
    "invalid_response",
}
_FORBIDDEN_PAYLOAD_KEYS = {
    "article_body",
    "full_article_body",
    "full_text",
    "html_body",
    "ocr_text",
    "raw_body",
    "raw_payload",
    "response_body",
}
_MAX_ROWS = 256
_MAX_PAYLOAD_BYTES = 128 * 1024
_MAX_PROVENANCE_BYTES = 32 * 1024


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
        raise ValueError("source snapshot content must be finite JSON") from exc


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, field: str, *, maximum: int = 256) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
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


def _date_text(value: Any, field: str) -> str:
    text = _required_text(value, field, maximum=10)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 date") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{field} must be a canonical ISO-8601 date")
    return text


def _contains_forbidden_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().casefold()
            if normalized in _FORBIDDEN_PAYLOAD_KEYS:
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


def _bounded_mapping(value: Any, field: str, *, maximum_bytes: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    result = dict(value)
    forbidden = _contains_forbidden_key(result)
    if forbidden is not None:
        raise ValueError(f"{field} contains forbidden field: {forbidden}")
    encoded = _canonical_json(result).encode("utf-8")
    if len(encoded) > maximum_bytes:
        raise ValueError(f"{field} exceeds its byte limit")
    return result


def _select_one(
    conn: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...],
) -> dict[str, Any] | None:
    cursor = conn.execute(query, parameters)
    source = cursor.fetchone()
    if source is None:
        return None
    if isinstance(source, sqlite3.Row):
        return dict(source)
    columns = [str(column[0]) for column in cursor.description or ()]
    return dict(zip(columns, source, strict=True))


def _decode(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["payload"] = json.loads(str(result.pop("payload_json")))
    result["provenance"] = json.loads(str(result.pop("provenance_json")))
    return result


def source_snapshot_receipt(
    conn: sqlite3.Connection,
    snapshot_id: str,
) -> dict[str, Any] | None:
    """Read one sealed source snapshot without schema creation or repair."""

    row = _select_one(
        conn,
        "SELECT * FROM single_track_v3_source_snapshot_receipt WHERE snapshot_id=?",
        (str(snapshot_id),),
    )
    return _decode(row) if row is not None else None


def source_snapshot_receipts_for_run(
    conn: sqlite3.Connection,
    run_id: str,
) -> list[dict[str, Any]]:
    """Read all immutable source snapshots bound to one retrieval run."""

    cursor = conn.execute(
        """
        SELECT * FROM single_track_v3_source_snapshot_receipt
        WHERE run_id=?
        ORDER BY source_key,target_entity_id,snapshot_id
        """,
        (str(run_id),),
    )
    columns = [str(column[0]) for column in cursor.description or ()]
    return [
        _decode(dict(zip(columns, source, strict=True)))
        for source in cursor.fetchall()
    ]


def source_snapshot_statuses(
    receipts: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> dict[str, str]:
    """Collapse receipts conservatively; one failed entity keeps a source incomplete."""

    priority = (
        "invalid_response",
        "unavailable",
        "source_delayed",
        "stale",
        "partial",
    )
    grouped: dict[str, list[str]] = {}
    for receipt in receipts:
        source_key = _required_text(receipt.get("source_key"), "source_key")
        if source_key not in SOURCE_SNAPSHOT_BINDINGS:
            raise ValueError("source_key is outside the source snapshot contract")
        status = _required_text(receipt.get("snapshot_status"), "snapshot_status")
        if status not in _SNAPSHOT_STATUSES:
            raise ValueError("snapshot_status is invalid")
        grouped.setdefault(source_key, []).append(status)
    result: dict[str, str] = {}
    for source_key, statuses in grouped.items():
        if all(status == "ok" for status in statuses):
            result[source_key] = "ok"
            continue
        result[source_key] = next(
            (status for status in priority if status in statuses),
            "invalid_response",
        )
    return {key: result[key] for key in sorted(result)}


def seal_source_snapshot_receipt(
    conn: sqlite3.Connection,
    receipt: Mapping[str, Any],
    *,
    ensure_schema: bool = True,
) -> dict[str, Any]:
    """Seal one bounded snapshot whose source availability is proven at the run cutoff."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    row = dict(receipt)
    run_id = _required_text(row.get("run_id"), "run_id")
    run = _select_one(conn, "SELECT * FROM news_retrieval_run WHERE run_id=?", (run_id,))
    if run is None:
        raise ValueError("run_id must reference a retrieval run")

    source_key = _required_text(row.get("source_key"), "source_key")
    binding = SOURCE_SNAPSHOT_BINDINGS.get(source_key)
    if binding is None:
        raise ValueError("source_key is outside the source snapshot contract")
    scope_key = _required_text(row.get("scope_key"), "scope_key")
    if scope_key != binding["scope_key"]:
        raise ValueError("scope_key does not match source_key")
    authority_tier = _required_text(row.get("authority_tier"), "authority_tier")
    if authority_tier not in binding["authority_tiers"]:
        raise ValueError("authority_tier does not match source_key")

    target_entity_id = _required_text(
        row.get("target_entity_id"), "target_entity_id", maximum=128
    )
    if binding["entity_scoped"] and target_entity_id == "*":
        raise ValueError("entity-scoped source snapshots require a target entity")
    target_trade_date = _date_text(row.get("target_trade_date"), "target_trade_date")
    if target_trade_date != str(run.get("target_trade_date")):
        raise ValueError("source snapshot target_trade_date must match its run")
    cutoff_text = _required_text(row.get("cutoff_at"), "cutoff_at")
    cutoff_at = _aware_timestamp(cutoff_text, "cutoff_at")
    if cutoff_text != str(run.get("cutoff_at")):
        raise ValueError("source snapshot cutoff_at must exactly match its run")

    status = _required_text(row.get("snapshot_status"), "snapshot_status")
    if status not in _SNAPSHOT_STATUSES:
        raise ValueError("snapshot_status is invalid")
    payload = _bounded_mapping(
        row.get("payload", {}), "payload", maximum_bytes=_MAX_PAYLOAD_BYTES
    )
    provenance = _bounded_mapping(
        row.get("provenance", {}),
        "provenance",
        maximum_bytes=_MAX_PROVENANCE_BYTES,
    )
    payload_rows = payload.get("rows", [])
    if not isinstance(payload_rows, list):
        raise ValueError("payload.rows must be a list")
    if len(payload_rows) > _MAX_ROWS:
        raise ValueError("payload.rows exceeds its item limit")
    row_count = int(row.get("row_count") if row.get("row_count") is not None else len(payload_rows))
    if row_count != len(payload_rows):
        raise ValueError("row_count must exactly match payload.rows")

    available_text = row.get("available_at")
    available_at = (
        _aware_timestamp(available_text, "available_at")
        if available_text is not None
        else None
    )
    source_as_of_date = row.get("source_as_of_date")
    if source_as_of_date is not None:
        source_as_of_date = _date_text(source_as_of_date, "source_as_of_date")
    if row_count:
        if available_at is None or source_as_of_date is None:
            raise ValueError("snapshot rows require as-of date and available_at")
        if status in {"unavailable", "invalid_response"}:
            raise ValueError("unavailable or invalid snapshots cannot retain data rows")
    elif status in {"ok", "partial", "stale"}:
        raise ValueError("usable or stale source snapshots require data rows")
    if available_at is not None and available_at > cutoff_at:
        raise ValueError("source snapshot is not available at the run cutoff")

    availability_reason = row.get("availability_reason")
    if status == "ok":
        if availability_reason not in (None, ""):
            raise ValueError("ok source snapshots cannot have an availability reason")
        availability_reason = None
    else:
        availability_reason = _required_text(
            availability_reason, "availability_reason", maximum=256
        )
    sealed_text = _required_text(row.get("sealed_at"), "sealed_at")
    created_text = _required_text(row.get("created_at"), "created_at")
    sealed_at = _aware_timestamp(sealed_text, "sealed_at")
    created_at = _aware_timestamp(created_text, "created_at")
    if created_at > sealed_at:
        raise ValueError("created_at cannot be later than sealed_at")
    if available_at is not None and sealed_at < available_at:
        raise ValueError("sealed_at cannot precede source availability")
    publisher_published_text = row.get("publisher_published_at")
    publisher_published_at = (
        _aware_timestamp(publisher_published_text, "publisher_published_at")
        if publisher_published_text is not None
        else None
    )
    first_seen_text = str(row.get("first_seen_at") or created_text)
    first_seen_at = _aware_timestamp(first_seen_text, "first_seen_at")
    usable_from_text = row.get("usable_from")
    if usable_from_text is None and available_text is not None:
        usable_from_text = available_text
    usable_from = (
        _aware_timestamp(usable_from_text, "usable_from")
        if usable_from_text is not None
        else None
    )
    if publisher_published_at is not None and available_at is not None and publisher_published_at > available_at:
        raise ValueError("publisher_published_at cannot be later than available_at")
    if first_seen_at > sealed_at:
        raise ValueError("first_seen_at cannot be later than sealed_at")
    if usable_from is not None:
        if usable_from > cutoff_at:
            raise ValueError("usable_from cannot be later than the run cutoff")
        if available_at is not None and usable_from < available_at:
            raise ValueError("usable_from cannot precede available_at")
    source_policy_version = _required_text(
        row.get("source_policy_version") or run.get("source_policy_version"),
        "source_policy_version",
    )

    identity = {
        "contract": SOURCE_SNAPSHOT_CONTRACT_VERSION,
        "run_id": run_id,
        "source_key": source_key,
        "target_entity_id": target_entity_id,
        "cutoff_at": cutoff_text,
    }
    snapshot_key = _sha(identity)
    supplied_key = row.get("snapshot_key")
    if supplied_key is not None and str(supplied_key).casefold() != snapshot_key:
        raise ValueError("snapshot_key does not match canonical identity")
    snapshot_id = f"source-snapshot:{snapshot_key}"
    if row.get("snapshot_id") is not None and str(row["snapshot_id"]) != snapshot_id:
        raise ValueError("snapshot_id does not match canonical identity")
    payload_digest = _sha(payload)
    if row.get("payload_digest") is not None and str(row["payload_digest"]).casefold() != payload_digest:
        raise ValueError("payload_digest does not match canonical payload")
    source_revision_id = str(
        row.get("source_revision_id")
        or "source-revision:"
        + _sha(
            {
                "source_id": row.get("source_id"),
                "source_as_of_date": source_as_of_date,
                "publisher_published_at": publisher_published_text,
                "payload_digest": payload_digest,
            }
        )
    )
    digest_payload = {
        **identity,
        "scope_key": scope_key,
        "target_trade_date": target_trade_date,
        "source_as_of_date": source_as_of_date,
        "source_revision_id": source_revision_id,
        "publisher_published_at": publisher_published_text,
        "available_at": available_text,
        "first_seen_at": first_seen_text,
        "usable_from": usable_from_text,
        "source_policy_version": source_policy_version,
        "source_id": _required_text(row.get("source_id"), "source_id", maximum=128),
        "authority_tier": authority_tier,
        "source_quality": _required_text(
            row.get("source_quality"), "source_quality", maximum=64
        ),
        "snapshot_status": status,
        "availability_reason": availability_reason,
        "row_count": row_count,
        "payload_digest": payload_digest,
        "provenance": provenance,
        "sealed_at": sealed_text,
    }
    snapshot_digest = _sha(digest_payload)
    if row.get("snapshot_digest") is not None and str(row["snapshot_digest"]).casefold() != snapshot_digest:
        raise ValueError("snapshot_digest does not match canonical content")

    values = {
        "snapshot_id": snapshot_id,
        "snapshot_key": snapshot_key,
        "run_id": run_id,
        "source_key": source_key,
        "scope_key": scope_key,
        "target_entity_id": target_entity_id,
        "target_trade_date": target_trade_date,
        "cutoff_at": cutoff_text,
        "source_as_of_date": source_as_of_date,
        "source_revision_id": source_revision_id,
        "publisher_published_at": publisher_published_text,
        "available_at": available_text,
        "first_seen_at": first_seen_text,
        "usable_from": usable_from_text,
        "source_policy_version": source_policy_version,
        "source_id": digest_payload["source_id"],
        "authority_tier": authority_tier,
        "source_quality": digest_payload["source_quality"],
        "snapshot_status": status,
        "availability_reason": availability_reason,
        "row_count": row_count,
        "payload_json": _canonical_json(payload),
        "provenance_json": _canonical_json(provenance),
        "payload_digest": payload_digest,
        "snapshot_digest": snapshot_digest,
        "sealed_at": sealed_text,
        "created_at": created_text,
    }
    columns = tuple(values)
    try:
        conn.execute(
            f"INSERT INTO single_track_v3_source_snapshot_receipt({','.join(columns)}) "
            f"VALUES({','.join(':' + column for column in columns)}) "
            "ON CONFLICT(snapshot_key) DO NOTHING",
            values,
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("source snapshot identity conflicts with existing content") from exc
    saved = _select_one(
        conn,
        "SELECT * FROM single_track_v3_source_snapshot_receipt WHERE snapshot_key=?",
        (snapshot_key,),
    )
    if saved is None:
        raise RuntimeError("source snapshot receipt could not be read back")
    if any(saved.get(column) != values.get(column) for column in columns):
        raise ValueError("source snapshot identity already exists with different content")
    return _decode(saved)
