from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from core.single_track_v3_schema import (
    TECHNICAL_COMPONENT_RETENTION_TRADING_DAYS,
    ensure_single_track_v3_schema,
)


_ARTIFACT_JSON_FIELDS = {
    "component_snapshot_ids": "component_snapshot_ids_json",
    "source_revision_ids": "source_revision_ids_json",
    "coverage": "coverage_json",
    "omissions": "omissions_json",
    "conflicts": "conflicts_json",
    "superseded_by_event_ids": "superseded_by_event_ids_json",
    "canonical_payload": "canonical_payload_json",
}

_ARTIFACT_JSON_DEFAULTS = {
    "component_snapshot_ids": [],
    "source_revision_ids": [],
    "coverage": {},
    "omissions": [],
    "conflicts": [],
    "superseded_by_event_ids": [],
    "canonical_payload": {},
}

_MODEL_ANSWER_JSON_FIELDS = {
    "explanation_blocks": "explanation_blocks_json",
    "used_event_ids": "used_event_ids_json",
    "research_limitations": "research_limitations_json",
}

_MODEL_ANSWER_JSON_DEFAULTS = {
    "explanation_blocks": [],
    "used_event_ids": [],
    "research_limitations": [],
}


def _json(value: Any, default: Any) -> str:
    normalized = default if value is None else value
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return default
    return parsed


def _aware_timestamp(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def canonical_answer_hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def normalize_stock_alias(alias: str) -> str:
    return "".join(unicodedata.normalize("NFKC", str(alias)).casefold().split())


def upsert_technical_components(
    conn: sqlite3.Connection,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    ensure_single_track_v3_schema(conn)
    values = []
    columns = (
        "trade_date",
        "stock_code",
        "indicator_key",
        "component_key",
        "value",
        "value_text",
        "unit",
        "parameters_json",
        "formula_version",
        "input_snapshot_digest",
        "input_start_date",
        "input_end_date",
        "input_row_count",
        "adjustment_basis",
        "source_quality",
        "data_quality",
        "availability_reason",
        "decision_ready",
        "quality_reason",
        "computed_at",
    )
    for source in rows:
        row = dict(source)
        row["stock_code"] = str(row.get("stock_code") or "").zfill(4)
        row["parameters_json"] = _json(row.get("parameters"), {})
        values.append({column: row.get(column) for column in columns})
    if not values:
        return 0
    before = conn.total_changes
    update_columns = [
        column
        for column in columns
        if column
        not in {"trade_date", "stock_code", "indicator_key", "component_key", "formula_version"}
    ]
    content_columns = [column for column in update_columns if column != "computed_at"]
    conn.executemany(
        f"""
        INSERT INTO technical_indicator_component({','.join(columns)})
        VALUES({','.join(':' + column for column in columns)})
        ON CONFLICT(trade_date,stock_code,indicator_key,component_key,formula_version)
        DO UPDATE SET {','.join(f'{column}=excluded.{column}' for column in update_columns)}
        WHERE {' OR '.join(f'technical_indicator_component.{column} IS NOT excluded.{column}' for column in content_columns)}
        """,
        values,
    )
    return conn.total_changes - before


def upsert_technical_states(
    conn: sqlite3.Connection,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    ensure_single_track_v3_schema(conn)
    values = []
    for source in rows:
        row = dict(source)
        values.append(
            {
                **row,
                "stock_code": str(row.get("stock_code") or "").zfill(4),
                "state_json": _json(row.get("state"), {}),
            }
        )
    if not values:
        return 0
    before = conn.total_changes
    conn.executemany(
        """
        INSERT INTO technical_indicator_state(
            stock_code,indicator_key,state_key,formula_version,state_value,
            state_json,last_trade_date,input_snapshot_digest,updated_at
        ) VALUES(
            :stock_code,:indicator_key,:state_key,:formula_version,:state_value,
            :state_json,:last_trade_date,:input_snapshot_digest,:updated_at
        )
        ON CONFLICT(stock_code,indicator_key,state_key,formula_version) DO UPDATE SET
            state_value=excluded.state_value,
            state_json=excluded.state_json,
            last_trade_date=excluded.last_trade_date,
            input_snapshot_digest=excluded.input_snapshot_digest,
            updated_at=excluded.updated_at
        WHERE technical_indicator_state.state_value IS NOT excluded.state_value
           OR technical_indicator_state.state_json IS NOT excluded.state_json
           OR technical_indicator_state.last_trade_date IS NOT excluded.last_trade_date
           OR technical_indicator_state.input_snapshot_digest IS NOT excluded.input_snapshot_digest
        """,
        values,
    )
    return conn.total_changes - before


def upsert_technical_vectors(
    conn: sqlite3.Connection,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    """Persist one compact, versioned full indicator vector per stock and date."""

    ensure_single_track_v3_schema(conn)
    values = []
    columns = (
        "trade_date",
        "stock_code",
        "formula_version",
        "ensemble_version",
        "values_json",
        "unavailable_json",
        "component_count",
        "decision_ready_count",
        "input_snapshot_digest",
        "input_start_date",
        "input_end_date",
        "input_row_count",
        "adjustment_basis",
        "source_quality",
        "data_quality",
        "quality_reason",
        "computed_at",
    )
    for source in rows:
        row = dict(source)
        row["stock_code"] = str(row.get("stock_code") or "").zfill(4)
        row["values_json"] = _json(row.pop("values", None), {})
        row["unavailable_json"] = _json(row.pop("unavailable", None), {})
        values.append({column: row.get(column) for column in columns})
    if not values:
        return 0
    before = conn.total_changes
    update_columns = [
        column
        for column in columns
        if column not in {"trade_date", "stock_code", "formula_version"}
    ]
    content_columns = [column for column in update_columns if column != "computed_at"]
    conn.executemany(
        f"""
        INSERT INTO technical_indicator_vector_daily({','.join(columns)})
        VALUES({','.join(':' + column for column in columns)})
        ON CONFLICT(trade_date,stock_code,formula_version) DO UPDATE SET
            {','.join(f'{column}=excluded.{column}' for column in update_columns)}
        WHERE {' OR '.join(f'technical_indicator_vector_daily.{column} IS NOT excluded.{column}' for column in content_columns)}
        """,
        values,
    )
    return conn.total_changes - before


def technical_components_for_snapshot(
    conn: sqlite3.Connection,
    stock_code: str,
    trade_date: str,
    *,
    formula_version: str,
) -> list[dict[str, Any]]:
    ensure_single_track_v3_schema(conn)
    rows = conn.execute(
        """
        SELECT * FROM technical_indicator_component
        WHERE stock_code=? AND trade_date=? AND formula_version=?
        ORDER BY indicator_key,component_key
        """,
        (str(stock_code).zfill(4), str(trade_date), str(formula_version)),
    ).fetchall()
    result = []
    for source in rows:
        row = dict(source)
        row["parameters"] = _json_load(row.pop("parameters_json"), {})
        result.append(row)
    return result


def prune_technical_components(
    conn: sqlite3.Connection,
    *,
    retain_trading_days: int = TECHNICAL_COMPONENT_RETENTION_TRADING_DAYS,
) -> dict[str, Any]:
    ensure_single_track_v3_schema(conn)
    keep = max(int(retain_trading_days), TECHNICAL_COMPONENT_RETENTION_TRADING_DAYS)
    dates = [
        str(row[0])
        for row in conn.execute(
            """
            SELECT DISTINCT trade_date FROM technical_indicator_component
            ORDER BY trade_date DESC LIMIT ?
            """,
            (keep,),
        ).fetchall()
    ]
    if len(dates) < keep:
        return {"retained_trading_days": keep, "cutoff_trade_date": dates[-1] if dates else None, "deleted_rows": 0}
    cutoff = dates[-1]
    before = conn.total_changes
    conn.execute("DELETE FROM technical_indicator_component WHERE trade_date<?", (cutoff,))
    return {
        "retained_trading_days": keep,
        "cutoff_trade_date": cutoff,
        "deleted_rows": conn.total_changes - before,
    }


def prune_technical_vectors(
    conn: sqlite3.Connection,
    *,
    retain_trading_days: int = TECHNICAL_COMPONENT_RETENTION_TRADING_DAYS,
) -> dict[str, Any]:
    ensure_single_track_v3_schema(conn)
    keep = max(int(retain_trading_days), TECHNICAL_COMPONENT_RETENTION_TRADING_DAYS)
    dates = [
        str(row[0])
        for row in conn.execute(
            """
            SELECT DISTINCT trade_date FROM technical_indicator_vector_daily
            ORDER BY trade_date DESC LIMIT ?
            """,
            (keep,),
        ).fetchall()
    ]
    if len(dates) < keep:
        return {
            "retained_trading_days": keep,
            "cutoff_trade_date": dates[-1] if dates else None,
            "deleted_rows": 0,
        }
    cutoff = dates[-1]
    before = conn.total_changes
    conn.execute("DELETE FROM technical_indicator_vector_daily WHERE trade_date<?", (cutoff,))
    return {
        "retained_trading_days": keep,
        "cutoff_trade_date": cutoff,
        "deleted_rows": conn.total_changes - before,
    }


def record_event_scan(conn: sqlite3.Connection, scan: Mapping[str, Any]) -> None:
    ensure_single_track_v3_schema(conn)
    row = dict(scan)
    conn.execute(
        """
        INSERT INTO event_scan_record(
            scan_id,entity_refs_json,request_received_at,analysis_cutoff,event_watermark,
            scan_state,source_policy_version,coverage_json,omissions_json,conflicts_json,
            completed_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(scan_id) DO UPDATE SET
            entity_refs_json=excluded.entity_refs_json,
            request_received_at=excluded.request_received_at,
            analysis_cutoff=excluded.analysis_cutoff,
            event_watermark=excluded.event_watermark,
            scan_state=excluded.scan_state,
            source_policy_version=excluded.source_policy_version,
            coverage_json=excluded.coverage_json,
            omissions_json=excluded.omissions_json,
            conflicts_json=excluded.conflicts_json,
            completed_at=excluded.completed_at
        """,
        (
            row["scan_id"],
            _json(row.get("entity_refs"), []),
            row["request_received_at"],
            row["analysis_cutoff"],
            row.get("event_watermark"),
            row["scan_state"],
            row["source_policy_version"],
            _json(row.get("coverage"), {}),
            _json(row.get("omissions"), []),
            _json(row.get("conflicts"), []),
            row["completed_at"],
        ),
    )


def upsert_event_evidence(
    conn: sqlite3.Connection,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    ensure_single_track_v3_schema(conn)
    columns = (
        "event_id",
        "entity_refs_json",
        "event_type",
        "source_id",
        "source_class",
        "publisher",
        "source_url",
        "publisher_published_at",
        "index_seen_at",
        "retrieved_at",
        "available_at",
        "effective_tw_trade_date",
        "verification_state",
        "rights",
        "untrusted_text",
        "fingerprint",
        "dedup_cluster",
        "relevance",
        "directness",
        "materiality",
        "magnitude",
        "surprise",
        "direction",
        "confidence",
        "horizon",
        "affected_claim_ids_json",
        "invalidation_scope",
        "corroborating_event_ids_json",
        "recorded_at",
    )
    values = []
    for source in rows:
        row = dict(source)
        row["entity_refs_json"] = _json(row.get("entity_refs"), [])
        row["affected_claim_ids_json"] = _json(row.get("affected_claim_ids"), [])
        row["corroborating_event_ids_json"] = _json(row.get("corroborating_event_ids"), [])
        values.append({column: row.get(column) for column in columns})
    if not values:
        return 0
    before = conn.total_changes
    conn.executemany(
        f"""
        INSERT INTO canonical_event_evidence({','.join(columns)})
        VALUES({','.join(':' + column for column in columns)})
        ON CONFLICT(event_id) DO UPDATE SET
            {','.join(f'{column}=excluded.{column}' for column in columns if column != 'event_id')}
        """,
        values,
    )
    return conn.total_changes - before


def events_available_at_cutoff(
    conn: sqlite3.Connection,
    analysis_cutoff: str,
    *,
    verification_state: str | None = None,
) -> list[dict[str, Any]]:
    ensure_single_track_v3_schema(conn)
    cutoff = _aware_timestamp(analysis_cutoff, "analysis_cutoff")
    if verification_state:
        rows = conn.execute(
            "SELECT * FROM canonical_event_evidence WHERE verification_state=? ORDER BY available_at,event_id",
            (verification_state,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM canonical_event_evidence ORDER BY available_at,event_id"
        ).fetchall()
    result = []
    for source in rows:
        row = dict(source)
        if _aware_timestamp(row["available_at"], "event.available_at") > cutoff:
            continue
        for field, default in (
            ("entity_refs_json", []),
            ("affected_claim_ids_json", []),
            ("corroborating_event_ids_json", []),
        ):
            row[field.removesuffix("_json")] = _json_load(row.pop(field), default)
        result.append(row)
    return result


def upsert_evidence_facts(
    conn: sqlite3.Connection,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    ensure_single_track_v3_schema(conn)
    columns = (
        "fact_id",
        "entity",
        "field",
        "value_json",
        "unit_currency",
        "unit",
        "period",
        "trade_date",
        "as_of",
        "available_at",
        "first_seen_at",
        "usable_from",
        "source_revision_id",
        "source_market_timestamp",
        "session",
        "adjustment_basis",
        "authority_tier",
        "quality",
        "availability_reason",
        "use_scope",
        "snapshot_id",
        "provenance_json",
        "formula_source_version",
        "recorded_at",
    )
    values = []
    for source in rows:
        row = dict(source)
        row["value_json"] = _json(row.get("value"), None)
        row["provenance_json"] = _json(row.get("provenance"), {})
        values.append({column: row.get(column) for column in columns})
    if not values:
        return 0
    before = conn.total_changes
    conn.executemany(
        f"""
        INSERT INTO canonical_evidence_fact({','.join(columns)})
        VALUES({','.join(':' + column for column in columns)})
        ON CONFLICT(fact_id) DO UPDATE SET
            {','.join(f'{column}=excluded.{column}' for column in columns if column != 'fact_id')}
        """,
        values,
    )
    return conn.total_changes - before


def seal_canonical_analysis_artifact(
    conn: sqlite3.Connection,
    artifact: Mapping[str, Any],
    *,
    fact_claim_ids: Mapping[str, str | None] | None = None,
    event_use_scopes: Mapping[str, str] | None = None,
) -> str:
    """Seal one immutable artifact after enforcing point-in-time evidence eligibility."""

    ensure_single_track_v3_schema(conn)
    row = dict(artifact)
    analysis_id = str(row.get("analysis_id") or "")
    if not analysis_id:
        raise ValueError("analysis_id is required")
    cutoff = _aware_timestamp(row.get("analysis_cutoff"), "analysis_cutoff")
    answer_text = str(row.get("canonical_answer_text") or "")
    expected_hash = canonical_answer_hash(answer_text)
    supplied_hash = str(row.get("canonical_answer_text_hash") or expected_hash)
    if supplied_hash != expected_hash:
        raise ValueError("canonical_answer_text_hash does not match canonical_answer_text")
    for digest_field in (
        "normalized_request_key",
        "canonical_core_digest",
        "projection_digest",
        "render_digest",
    ):
        digest = row.get(digest_field)
        if digest in (None, ""):
            row[digest_field] = None
            continue
        digest = str(digest).lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"{digest_field} must be a lowercase SHA-256 digest")
        row[digest_field] = digest
    row.setdefault("artifact_contract_version", "unversioned")

    fact_map = dict(fact_claim_ids or {})
    event_map = dict(event_use_scopes or {})
    if fact_map:
        placeholders = ",".join("?" for _ in fact_map)
        facts = conn.execute(
            f"SELECT fact_id,available_at FROM canonical_evidence_fact WHERE fact_id IN ({placeholders})",
            list(fact_map),
        ).fetchall()
        found = {str(item[0]): str(item[1]) for item in facts}
        missing = sorted(set(fact_map) - set(found))
        if missing:
            raise ValueError(f"unknown fact ids: {','.join(missing)}")
        late = sorted(
            fact_id
            for fact_id, available_at in found.items()
            if _aware_timestamp(available_at, "fact.available_at") > cutoff
        )
        if late:
            raise ValueError(f"facts after analysis cutoff: {','.join(late)}")
    if event_map:
        placeholders = ",".join("?" for _ in event_map)
        events = conn.execute(
            f"SELECT event_id,available_at FROM canonical_event_evidence WHERE event_id IN ({placeholders})",
            list(event_map),
        ).fetchall()
        found = {str(item[0]): str(item[1]) for item in events}
        missing = sorted(set(event_map) - set(found))
        if missing:
            raise ValueError(f"unknown event ids: {','.join(missing)}")
        late = sorted(
            event_id
            for event_id, available_at in found.items()
            if _aware_timestamp(available_at, "event.available_at") > cutoff
        )
        if late:
            raise ValueError(f"events after analysis cutoff: {','.join(late)}")

    columns = (
        "analysis_id",
        "snapshot_id",
        "snapshot_digest",
        "request_received_at",
        "analysis_cutoff",
        "snapshot_sealed_at",
        "context_digest",
        "component_snapshot_ids_json",
        "source_revision_ids_json",
        "normalized_request_key",
        "target_entity_id",
        "target_trade_date",
        "analysis_session",
        "selected_profile",
        "canonical_core_digest",
        "projection_digest",
        "render_digest",
        "artifact_contract_version",
        "event_watermark",
        "source_policy_version",
        "weight_version",
        "formula_version",
        "referee_version",
        "model_digest",
        "prompt_version",
        "validator_version",
        "renderer_version",
        "entity_registry_version",
        "conversation_projection_version",
        "response_style_version",
        "coverage_json",
        "omissions_json",
        "conflicts_json",
        "validity",
        "superseded_by_event_ids_json",
        "superseded_reason",
        "canonical_payload_json",
        "canonical_answer_text",
        "canonical_answer_text_hash",
        "created_at",
    )
    for source_field, database_field in _ARTIFACT_JSON_FIELDS.items():
        default = _ARTIFACT_JSON_DEFAULTS[source_field]
        row[database_field] = _json(row.get(source_field), default)
    row["canonical_answer_text_hash"] = supplied_hash
    conn.execute(
        f"""
        INSERT INTO canonical_analysis_artifact({','.join(columns)})
        VALUES({','.join(':' + column for column in columns)})
        """,
        {column: row.get(column) for column in columns},
    )
    if fact_map:
        conn.executemany(
            "INSERT INTO canonical_analysis_fact(analysis_id,fact_id,claim_id) VALUES(?,?,?)",
            [(analysis_id, fact_id, claim_id) for fact_id, claim_id in fact_map.items()],
        )
    if event_map:
        conn.executemany(
            "INSERT INTO canonical_analysis_event(analysis_id,event_id,use_scope) VALUES(?,?,?)",
            [(analysis_id, event_id, scope) for event_id, scope in event_map.items()],
        )
    return analysis_id


def canonical_analysis_artifact(
    conn: sqlite3.Connection,
    analysis_id: str,
    *,
    ensure_schema: bool = True,
) -> dict[str, Any] | None:
    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    source = conn.execute(
        "SELECT * FROM canonical_analysis_artifact WHERE analysis_id=?",
        (str(analysis_id),),
    ).fetchone()
    if not source:
        return None
    row = dict(source)
    for public_field, database_field in _ARTIFACT_JSON_FIELDS.items():
        default = _ARTIFACT_JSON_DEFAULTS[public_field]
        row[public_field] = _json_load(row.pop(database_field), default)
    row["fact_ids"] = [
        str(item[0])
        for item in conn.execute(
            "SELECT fact_id FROM canonical_analysis_fact WHERE analysis_id=? ORDER BY fact_id",
            (str(analysis_id),),
        ).fetchall()
    ]
    row["event_ids"] = [
        str(item[0])
        for item in conn.execute(
            "SELECT event_id FROM canonical_analysis_event WHERE analysis_id=? ORDER BY event_id",
            (str(analysis_id),),
        ).fetchall()
    ]
    row["canonical_model_answer"] = canonical_model_answer_extension(
        conn,
        str(analysis_id),
        ensure_schema=False,
    )
    return row


def seal_canonical_model_answer_extension(
    conn: sqlite3.Connection,
    extension: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist one immutable, validator-approved model answer for an artifact."""

    ensure_single_track_v3_schema(conn)
    row = dict(extension)
    analysis_id = str(row.get("analysis_id") or "").strip()
    if not analysis_id:
        raise ValueError("analysis_id is required")
    artifact = conn.execute(
        """
        SELECT snapshot_id,analysis_cutoff,validity,canonical_answer_text_hash
        FROM canonical_analysis_artifact WHERE analysis_id=?
        """,
        (analysis_id,),
    ).fetchone()
    if artifact is None:
        raise ValueError("canonical analysis artifact not found")
    artifact_row = {
        "snapshot_id": artifact[0],
        "analysis_cutoff": artifact[1],
        "validity": artifact[2],
        "canonical_answer_text_hash": artifact[3],
    }
    if str(artifact_row.get("validity") or "") not in {"valid", "partial"}:
        raise ValueError("canonical analysis artifact is not eligible for model finalization")
    base_hash = str(row.get("base_answer_text_hash") or "")
    if base_hash != str(artifact_row.get("canonical_answer_text_hash") or ""):
        raise ValueError("base answer hash does not match canonical analysis artifact")
    answer_text = str(row.get("canonical_answer_text") or "")
    if not answer_text:
        raise ValueError("canonical model answer text is required")
    answer_hash = canonical_answer_hash(answer_text)
    if str(row.get("canonical_answer_text_hash") or answer_hash) != answer_hash:
        raise ValueError("canonical model answer hash does not match answer text")
    row["canonical_answer_text_hash"] = answer_hash
    _aware_timestamp(row.get("created_at"), "created_at")

    digest_fields = (
        "base_answer_text_hash",
        "packet_digest",
        "compacted_packet_sha256",
        "model_digest",
        "generation_schema_sha256",
        "generation_prompt_sha256",
        "canonical_answer_text_hash",
        "release_source_digest",
    )
    for field in digest_fields:
        value = str(row.get(field) or "").lower()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError(f"{field} must be a lowercase SHA-256 digest")
        row[field] = value
    required_text_fields = (
        "model_id",
        "candidate_version",
        "generation_schema_version",
        "validator_version",
        "renderer_version",
        "authorization_id",
    )
    for field in required_text_fields:
        row[field] = str(row.get(field) or "").strip()
        if not row[field]:
            raise ValueError(f"{field} is required")
    columns = (
        "analysis_id",
        "base_answer_text_hash",
        "packet_digest",
        "compacted_packet_sha256",
        "model_id",
        "model_digest",
        "candidate_version",
        "generation_schema_version",
        "generation_schema_sha256",
        "generation_prompt_sha256",
        "validator_version",
        "renderer_version",
        "explanation_blocks_json",
        "used_event_ids_json",
        "research_limitations_json",
        "canonical_answer_text",
        "canonical_answer_text_hash",
        "authorization_id",
        "release_source_digest",
        "created_at",
    )
    for public_field, database_field in _MODEL_ANSWER_JSON_FIELDS.items():
        row[database_field] = _json(
            row.get(public_field),
            _MODEL_ANSWER_JSON_DEFAULTS[public_field],
        )
    values = {column: row.get(column) for column in columns}
    existing = canonical_model_answer_extension(conn, analysis_id, ensure_schema=False)
    if existing is not None:
        comparable = dict(existing)
        if all(
            comparable.get(column) == values.get(column)
            for column in columns
            if not column.endswith("_json") and column != "created_at"
        ) and all(
            comparable.get(public_field) == row.get(public_field, _MODEL_ANSWER_JSON_DEFAULTS[public_field])
            for public_field in _MODEL_ANSWER_JSON_FIELDS
        ):
            return existing
        raise ValueError("canonical model answer extension already exists with different content")
    conn.execute(
        f"""
        INSERT INTO canonical_model_answer_extension({','.join(columns)})
        VALUES({','.join(':' + column for column in columns)})
        """,
        values,
    )
    saved = canonical_model_answer_extension(conn, analysis_id, ensure_schema=False)
    if saved is None:
        raise RuntimeError("canonical model answer extension could not be read back")
    return saved


def canonical_model_answer_extension(
    conn: sqlite3.Connection,
    analysis_id: str,
    *,
    ensure_schema: bool = True,
) -> dict[str, Any] | None:
    """Read the immutable model-answer extension without altering the base artifact."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    source = conn.execute(
        "SELECT * FROM canonical_model_answer_extension WHERE analysis_id=?",
        (str(analysis_id),),
    ).fetchone()
    if source is None:
        return None
    row = dict(source)
    for public_field, database_field in _MODEL_ANSWER_JSON_FIELDS.items():
        row[public_field] = _json_load(
            row.pop(database_field),
            _MODEL_ANSWER_JSON_DEFAULTS[public_field],
        )
    return row


def supersede_artifact_with_events(
    conn: sqlite3.Connection,
    analysis_id: str,
    event_ids: Iterable[str],
    *,
    reason: str,
    ensure_schema: bool = True,
) -> list[str]:
    """Supersede an artifact only with later verified material events."""

    artifact = canonical_analysis_artifact(
        conn,
        analysis_id,
        ensure_schema=ensure_schema,
    )
    if artifact is None:
        raise ValueError("analysis artifact not found")
    cutoff = _aware_timestamp(artifact["analysis_cutoff"], "analysis_cutoff")
    selected = sorted({str(event_id) for event_id in event_ids if str(event_id)})
    if not selected:
        raise ValueError("at least one event_id is required")
    placeholders = ",".join("?" for _ in selected)
    rows = conn.execute(
        f"""
        SELECT event_id,available_at,verification_state,materiality
        FROM canonical_event_evidence WHERE event_id IN ({placeholders})
        """,
        selected,
    ).fetchall()
    found = {str(row[0]): row for row in rows}
    invalid = []
    for event_id in selected:
        row = found.get(event_id)
        if (
            row is None
            or str(row[2]) != "verified"
            or str(row[3]) != "material"
            or _aware_timestamp(row[1], "event.available_at") <= cutoff
        ):
            invalid.append(event_id)
    if invalid:
        raise ValueError(f"events cannot supersede artifact: {','.join(invalid)}")
    merged = sorted(set(artifact.get("superseded_by_event_ids") or []) | set(selected))
    conn.execute(
        """
        UPDATE canonical_analysis_artifact
        SET validity='superseded',superseded_by_event_ids_json=?,superseded_reason=?
        WHERE analysis_id=?
        """,
        (_json(merged, []), str(reason), str(analysis_id)),
    )
    return merged


def upsert_stock_entity_aliases(
    conn: sqlite3.Connection,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    ensure_single_track_v3_schema(conn)
    values = []
    for source in rows:
        row = dict(source)
        alias = str(row.get("alias") or "").strip()
        row["alias"] = alias
        row["alias_normalized"] = normalize_stock_alias(alias)
        row["stock_code"] = str(row.get("stock_code") or "").zfill(4)
        row["ambiguity_set_json"] = _json(row.get("ambiguity_set"), [])
        row["approved"] = int(bool(row.get("approved", False)))
        row.setdefault("approval_id", None)
        row.setdefault("source_evidence_digest", None)
        values.append(row)
    if not values:
        return 0
    before = conn.total_changes
    conn.executemany(
        """
        INSERT INTO stock_entity_alias(
            alias,alias_normalized,stock_code,canonical_name,trading_name,source,
            effective_from,effective_to,confidence,ambiguity_set_json,
            registry_version,reviewed,approved,approval_id,source_evidence_digest,updated_at
        ) VALUES(
            :alias,:alias_normalized,:stock_code,:canonical_name,:trading_name,:source,
            :effective_from,:effective_to,:confidence,:ambiguity_set_json,
            :registry_version,:reviewed,:approved,:approval_id,:source_evidence_digest,:updated_at
        )
        ON CONFLICT(alias_normalized,stock_code,effective_from,registry_version) DO UPDATE SET
            alias=excluded.alias,
            canonical_name=excluded.canonical_name,
            trading_name=excluded.trading_name,
            source=excluded.source,
            effective_to=excluded.effective_to,
            confidence=excluded.confidence,
            ambiguity_set_json=excluded.ambiguity_set_json,
            reviewed=excluded.reviewed,
            approved=excluded.approved,
            approval_id=excluded.approval_id,
            source_evidence_digest=excluded.source_evidence_digest,
            updated_at=excluded.updated_at
        """,
        values,
    )
    return conn.total_changes - before


def active_alias_candidates(
    conn: sqlite3.Connection,
    alias: str,
    *,
    effective_date: str,
    registry_version: str,
    reviewed_only: bool = True,
    approved_only: bool = False,
) -> list[dict[str, Any]]:
    ensure_single_track_v3_schema(conn)
    clauses = [
        "alias_normalized=?",
        "registry_version=?",
        "effective_from<=?",
        "(effective_to IS NULL OR effective_to>=?)",
    ]
    parameters: list[Any] = [
        normalize_stock_alias(alias),
        str(registry_version),
        str(effective_date),
        str(effective_date),
    ]
    if reviewed_only:
        clauses.append("reviewed=1")
    if approved_only:
        clauses.append("approved=1")
    rows = conn.execute(
        f"""
        SELECT * FROM stock_entity_alias WHERE {' AND '.join(clauses)}
        ORDER BY confidence DESC,stock_code
        """,
        parameters,
    ).fetchall()
    result = []
    for source in rows:
        row = dict(source)
        row["ambiguity_set"] = _json_load(row.pop("ambiguity_set_json"), [])
        result.append(row)
    return result


def record_target_predictions(
    conn: sqlite3.Connection,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    ensure_single_track_v3_schema(conn)
    values = []
    for source in rows:
        row = dict(source)
        row["probability_json"] = _json(row.get("probabilities"), {})
        row.setdefault("event_cluster_id", None)
        row.setdefault("event_type", None)
        row["large_safety_slice"] = int(bool(row.get("large_safety_slice")))
        row["synthetic"] = int(bool(row.get("synthetic")))
        row.setdefault("event_revision_id", None)
        row.setdefault("target_entity_id", None)
        row.setdefault("target_trade_date", None)
        row.setdefault("weight_version", "unversioned")
        row.setdefault("contribution_state", "shadow_zero_weight")
        values.append(row)
    if not values:
        return 0
    before = conn.total_changes
    conn.executemany(
        """
        INSERT INTO analysis_target_prediction(
            sample_id,analysis_id,model_role,target_key,regime,probability_json,
            eligible,completion_state,omission_reason,analysis_cutoff,created_at
            ,event_cluster_id,event_type,large_safety_slice,synthetic,
            event_revision_id,target_entity_id,target_trade_date,weight_version,
            contribution_state
        ) VALUES(
            :sample_id,:analysis_id,:model_role,:target_key,:regime,:probability_json,
            :eligible,:completion_state,:omission_reason,:analysis_cutoff,:created_at
            ,:event_cluster_id,:event_type,:large_safety_slice,:synthetic,
            :event_revision_id,:target_entity_id,:target_trade_date,:weight_version,
            :contribution_state
        )
        ON CONFLICT(sample_id,model_role,target_key) DO UPDATE SET
            analysis_id=excluded.analysis_id,
            regime=excluded.regime,
            probability_json=excluded.probability_json,
            eligible=excluded.eligible,
            completion_state=excluded.completion_state,
            omission_reason=excluded.omission_reason,
            analysis_cutoff=excluded.analysis_cutoff,
            event_cluster_id=excluded.event_cluster_id,
            event_type=excluded.event_type,
            large_safety_slice=excluded.large_safety_slice,
            synthetic=excluded.synthetic,
            event_revision_id=excluded.event_revision_id,
            target_entity_id=excluded.target_entity_id,
            target_trade_date=excluded.target_trade_date,
            weight_version=excluded.weight_version,
            contribution_state=excluded.contribution_state,
            created_at=excluded.created_at
        """,
        values,
    )
    return conn.total_changes - before


def record_target_outcomes(
    conn: sqlite3.Connection,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    ensure_single_track_v3_schema(conn)
    values = []
    for source in rows:
        row = dict(source)
        row["stock_code"] = str(row.get("stock_code") or "").zfill(4)
        values.append(row)
    if not values:
        return 0
    before = conn.total_changes
    conn.executemany(
        """
        INSERT INTO analysis_target_outcome(
            sample_id,target_key,outcome_revision,stock_code,prediction_trade_date,
            outcome_trade_date,label,t_close,next_open,next_close,adjustment_basis,
            quality_status,availability_reason,available_at,recorded_at
        ) VALUES(
            :sample_id,:target_key,:outcome_revision,:stock_code,:prediction_trade_date,
            :outcome_trade_date,:label,:t_close,:next_open,:next_close,:adjustment_basis,
            :quality_status,:availability_reason,:available_at,:recorded_at
        )
        ON CONFLICT(sample_id,target_key,outcome_revision) DO UPDATE SET
            stock_code=excluded.stock_code,
            prediction_trade_date=excluded.prediction_trade_date,
            outcome_trade_date=excluded.outcome_trade_date,
            label=excluded.label,
            t_close=excluded.t_close,
            next_open=excluded.next_open,
            next_close=excluded.next_close,
            adjustment_basis=excluded.adjustment_basis,
            quality_status=excluded.quality_status,
            availability_reason=excluded.availability_reason,
            available_at=excluded.available_at,
            recorded_at=excluded.recorded_at
        """,
        values,
    )
    return conn.total_changes - before
