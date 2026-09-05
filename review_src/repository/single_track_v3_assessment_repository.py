from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import math
import sqlite3
from typing import Any, Mapping

from core.single_track_v3_schema import ensure_single_track_v3_schema


_CONTENT_REQUIRED_RESULT_FIELDS = {
    "event_facts",
    "verification_state",
    "event_type",
    "materiality",
    "transmission_paths",
    "counterevidence",
    "uncertainty",
    "evidence_ids",
}
_CONTENT_FORBIDDEN_RESULT_FIELDS = {
    "target_entity_id",
    "target_trade_date",
    "forecast_target_id",
    "stock_code",
    "eligible_for_weight",
    "direction_probabilities",
    "direction_probabilities_json",
    "individual_stock_direction",
    "raw_article_body",
    "article_body",
    "raw_body",
    "full_text",
    "full_ocr",
    "image_bytes",
    "image_data",
}
_TARGET_REQUIRED_RESULT_FIELDS = {
    "target_materiality",
    "target_direction",
    "target_impact_magnitude",
    "regime_selection",
    "target_relationship_type",
    "drivers",
    "counterevidence",
    "uncertainty",
    "priced_in_state",
    "evidence_ids",
    "eligible_for_explanation",
    "eligible_for_weight",
    "calibration_state",
}
_TARGET_FORBIDDEN_RESULT_FIELDS = {
    "expected_percent_range",
    "expected_price_change",
    "predicted_return",
    "predicted_return_pct",
    "price_target",
    "target_price",
}
_DIRECTION_PROBABILITY_KEYS = {"positive", "neutral", "negative"}
_MAGNITUDE_PROBABILITY_KEYS = {"negligible", "low", "medium", "high", "extreme"}
_FORECAST_TARGETS = {
    "NEXT_SESSION_OPEN_GAP",
    "NEXT_SESSION_OPEN_TO_CLOSE_CONTINUATION",
    "NEXT_SESSION_CLOSE_DIRECTION",
}
_ATTEMPT_KINDS = {"initial", "retry", "contract_repair"}
_ATTEMPT_FAILURE_CLASSES = {
    "deadline_elapsed",
    "timeout",
    "worker_loss",
    "malformed",
    "validator_reject",
    "permanent",
}
_CONTENT_TERMINAL_REASONS = {
    "content_attempts_exhausted",
    "content_recovery_deadline_elapsed",
    "content_permanent_failure",
}
_RETRY_DELAY_SECONDS = (60, 300, 900)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json(value: Any, default: Any) -> str:
    if value is None:
        value = default
    return _canonical_json(value)


def _json_load(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _contains_forbidden_key(value: Any, forbidden: set[str]) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if normalized in forbidden:
                return normalized
            found = _contains_forbidden_key(nested, forbidden)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _contains_forbidden_key(nested, forbidden)
            if found is not None:
                return found
    return None


def _require_digest(value: Any, field: str) -> str:
    digest = str(value or "").lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return digest


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
    return parsed


def _timestamp(value: datetime) -> str:
    # DB-derived clocks include milliseconds.  Preserve non-zero precision so
    # serializing retry availability never shortens the contracted delay.
    return value.isoformat(timespec="auto")


def _database_now_tpe(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%f','now','+8 hours') || '+08:00'"
    ).fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("database clock is unavailable")
    value = str(row[0])
    _aware_timestamp(value, "database_now")
    return value


def _probability_bucket(
    value: Any,
    *,
    field: str,
    expected_keys: set[str],
) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(map(str, value.keys())) != expected_keys:
        raise ValueError(f"{field} must contain exactly {sorted(expected_keys)}")
    try:
        normalized = {str(key): float(number) for key, number in value.items()}
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} probabilities must be numeric") from exc
    if any(
        not math.isfinite(number) or number < 0 or number > 1
        for number in normalized.values()
    ):
        raise ValueError(f"{field} probabilities must be between 0 and 1")
    if abs(sum(normalized.values()) - 1.0) > 1e-6:
        raise ValueError(f"{field} probabilities must sum to 1")
    return normalized


def _boolean_flag(value: Any, field: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return int(value)
    raise ValueError(f"{field} must be a boolean")


def _string_list(value: Any, field: str, *, unique: bool = False) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    normalized = [_required_text(item, field) for item in value]
    if unique and len(set(normalized)) != len(normalized):
        raise ValueError(f"{field} must not contain duplicates")
    return normalized


def _retry_available_at(
    *,
    logical_key: str,
    attempt_no: int,
    failed_at: datetime,
) -> datetime | None:
    if attempt_no < 1 or attempt_no > len(_RETRY_DELAY_SECONDS):
        return None
    seed = hashlib.sha256(f"{logical_key}:{attempt_no}".encode("utf-8")).digest()
    jitter_seconds = int.from_bytes(seed[:2], "big") % 31
    return failed_at + timedelta(
        seconds=_RETRY_DELAY_SECONDS[attempt_no - 1] + jitter_seconds
    )


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


def _insert_idempotent(
    conn: sqlite3.Connection,
    *,
    table: str,
    identity_column: str,
    columns: tuple[str, ...],
    values: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        conn.execute(
            f"""
            INSERT INTO {table}({','.join(columns)})
            VALUES({','.join(':' + column for column in columns)})
            ON CONFLICT({identity_column}) DO NOTHING
            """,
            dict(values),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"{table} identity conflicts with existing content") from exc
    existing = _select_one(
        conn,
        f"SELECT * FROM {table} WHERE {identity_column}=?",
        (values[identity_column],),
    )
    if existing is None:
        raise RuntimeError(f"{table} could not be read back")
    if any(existing.get(column) != values.get(column) for column in columns):
        raise ValueError(f"{table} identity already exists with different content")
    return existing


def _begin_savepoint(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(f"SAVEPOINT {name}")


def _rollback_savepoint(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(f"ROLLBACK TO {name}")
    conn.execute(f"RELEASE {name}")


def _release_savepoint(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(f"RELEASE {name}")


def build_content_assessment_key(
    *,
    event_revision_id: str,
    content_evidence_digest: str,
    content_cutoff: str,
    model_digest: str,
    prompt_version: str,
    schema_version: str,
    validator_version: str,
    contract_version: str,
) -> str:
    """Build the stock- and target-independent content logical key."""

    _aware_timestamp(content_cutoff, "content_cutoff")
    payload = {
        "contract_version": _required_text(contract_version, "contract_version"),
        "content_cutoff": str(content_cutoff),
        "content_evidence_digest": _require_digest(
            content_evidence_digest, "content_evidence_digest"
        ),
        "event_revision_id": _required_text(event_revision_id, "event_revision_id"),
        "model_digest": _require_digest(model_digest, "model_digest"),
        "prompt_version": _required_text(prompt_version, "prompt_version"),
        "schema_version": _required_text(schema_version, "schema_version"),
        "validator_version": _required_text(validator_version, "validator_version"),
    }
    return _sha256(payload)


def build_target_impact_key(
    *,
    content_assessment_key: str,
    content_result_digest: str,
    target_entity_id: str,
    target_trade_date: str,
    forecast_target_id: str,
    target_fact_snapshot_digest: str,
    target_context_digest: str,
    model_digest: str,
    prompt_version: str,
    schema_version: str,
    validator_version: str,
    contract_version: str,
    calibrator_version: str,
    weight_version: str = "candidate-unreleased",
) -> str:
    """Build the per-target key from one sealed content result."""

    target = _required_text(forecast_target_id, "forecast_target_id")
    if target not in _FORECAST_TARGETS:
        raise ValueError("forecast_target_id is outside TargetLabelContractV1")
    payload = {
        "calibrator_version": _required_text(calibrator_version, "calibrator_version"),
        "content_assessment_key": _require_digest(
            content_assessment_key, "content_assessment_key"
        ),
        "content_result_digest": _require_digest(
            content_result_digest, "content_result_digest"
        ),
        "contract_version": _required_text(contract_version, "contract_version"),
        "forecast_target_id": target,
        "model_digest": _require_digest(model_digest, "model_digest"),
        "prompt_version": _required_text(prompt_version, "prompt_version"),
        "schema_version": _required_text(schema_version, "schema_version"),
        "target_context_digest": _require_digest(
            target_context_digest, "target_context_digest"
        ),
        "target_entity_id": _required_text(target_entity_id, "target_entity_id"),
        "target_fact_snapshot_digest": _require_digest(
            target_fact_snapshot_digest, "target_fact_snapshot_digest"
        ),
        "target_trade_date": _required_text(target_trade_date, "target_trade_date"),
        "validator_version": _required_text(validator_version, "validator_version"),
        "weight_version": _required_text(weight_version, "weight_version"),
    }
    return _sha256(payload)


def build_waiting_target_impact_key(
    *,
    content_assessment_key: str,
    target_entity_id: str,
    target_trade_date: str,
    forecast_target_id: str,
    target_fact_snapshot_digest: str,
    target_context_digest: str,
    model_digest: str,
    prompt_version: str,
    schema_version: str,
    validator_version: str,
    contract_version: str,
    calibrator_version: str,
    weight_version: str = "candidate-unreleased",
) -> str:
    """Build the immutable pre-content target job identity without pretending a result exists."""

    target = _required_text(forecast_target_id, "forecast_target_id")
    if target not in _FORECAST_TARGETS:
        raise ValueError("forecast_target_id is outside TargetLabelContractV1")
    return _sha256(
        {
            "calibrator_version": _required_text(calibrator_version, "calibrator_version"),
            "content_assessment_key": _require_digest(
                content_assessment_key, "content_assessment_key"
            ),
            "contract_version": _required_text(contract_version, "contract_version"),
            "forecast_target_id": target,
            "identity_stage": "waiting_for_content",
            "model_digest": _require_digest(model_digest, "model_digest"),
            "prompt_version": _required_text(prompt_version, "prompt_version"),
            "schema_version": _required_text(schema_version, "schema_version"),
            "target_context_digest": _require_digest(
                target_context_digest, "target_context_digest"
            ),
            "target_entity_id": _required_text(target_entity_id, "target_entity_id"),
            "target_fact_snapshot_digest": _require_digest(
                target_fact_snapshot_digest, "target_fact_snapshot_digest"
            ),
            "target_trade_date": _required_text(target_trade_date, "target_trade_date"),
            "validator_version": _required_text(validator_version, "validator_version"),
            "weight_version": _required_text(weight_version, "weight_version"),
        }
    )


def create_event_cluster(
    conn: sqlite3.Connection,
    item: Mapping[str, Any],
    *,
    ensure_schema: bool = True,
) -> dict[str, Any]:
    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    row = dict(item)
    for field in (
        "event_cluster_id",
        "dedup_key",
        "event_type",
        "first_available_at",
        "last_seen_at",
        "created_at",
        "updated_at",
    ):
        row[field] = _required_text(row.get(field), field)
    for field in ("first_available_at", "last_seen_at", "created_at", "updated_at"):
        _aware_timestamp(row[field], field)
    row["entity_refs_json"] = _json(row.get("entity_refs"), [])
    row["cluster_state"] = str(row.get("cluster_state") or "active")
    columns = (
        "event_cluster_id",
        "dedup_key",
        "event_type",
        "entity_refs_json",
        "cluster_state",
        "first_available_at",
        "last_seen_at",
        "created_at",
        "updated_at",
    )
    saved = _insert_idempotent(
        conn,
        table="event_cluster",
        identity_column="dedup_key",
        columns=columns,
        values={column: row.get(column) for column in columns},
    )
    saved["entity_refs"] = _json_load(saved.pop("entity_refs_json"), [])
    return saved


def create_event_revision(
    conn: sqlite3.Connection,
    item: Mapping[str, Any],
    *,
    ensure_schema: bool = True,
) -> dict[str, Any]:
    """Persist bounded evidence only; raw article body is not accepted or stored."""

    if ensure_schema:
        ensure_single_track_v3_schema(conn)
    row = dict(item)
    forbidden = _contains_forbidden_key(
        row,
        {"article_body", "raw_article_body", "raw_content", "full_text"},
    )
    if forbidden is not None:
        raise ValueError(f"{forbidden} retention is prohibited")
    for field in (
        "event_revision_id",
        "event_cluster_id",
        "revision_digest",
        "content_evidence_digest",
        "content_cutoff",
        "event_type",
        "verification_state",
        "materiality",
        "available_at",
        "hot_content_expires_at",
        "sealed_at",
        "created_at",
    ):
        row[field] = _required_text(row.get(field), field)
    row["revision_digest"] = _require_digest(row["revision_digest"], "revision_digest")
    row["content_evidence_digest"] = _require_digest(
        row["content_evidence_digest"], "content_evidence_digest"
    )
    row["revision_no"] = int(row.get("revision_no") or 0)
    if row["revision_no"] < 1:
        raise ValueError("revision_no must be at least 1")
    for field in (
        "content_cutoff",
        "available_at",
        "hot_content_expires_at",
        "sealed_at",
        "created_at",
    ):
        _aware_timestamp(row[field], field)
    available = _aware_timestamp(row["available_at"], "available_at")
    expires = _aware_timestamp(row["hot_content_expires_at"], "hot_content_expires_at")
    if expires < available or expires > available + timedelta(days=7):
        raise ValueError("hot content retention must be between 0 and 7 calendar days")
    row["short_excerpt"] = str(row.get("short_excerpt") or "")
    if len(row["short_excerpt"]) > 600:
        raise ValueError("short_excerpt exceeds the 600-character bound")
    row["key_points_json"] = _json(row.get("key_points"), [])
    row["source_refs_json"] = _json(row.get("source_refs"), [])
    row["price_reaction_json"] = _json(row.get("price_reaction"), {})
    base_columns = (
        "event_revision_id",
        "event_cluster_id",
        "revision_no",
        "revision_digest",
        "content_evidence_digest",
        "content_cutoff",
        "event_type",
        "verification_state",
        "materiality",
        "key_points_json",
        "short_excerpt",
        "source_refs_json",
        "price_reaction_json",
        "supersedes_revision_id",
        "available_at",
        "hot_content_expires_at",
        "sealed_at",
        "created_at",
    )
    existing_columns = {
        str(source[1])
        for source in conn.execute("PRAGMA table_info(event_revision)").fetchall()
    }
    v11_columns: tuple[str, ...] = ()
    if "content_materiality" in existing_columns:
        content_materiality = str(
            row.get("content_materiality") or "unknown_pending"
        )
        if content_materiality not in {
            "critical",
            "high",
            "medium",
            "low",
            "unknown_pending",
        }:
            raise ValueError("content_materiality is invalid")
        row["content_materiality"] = content_materiality
        row["materiality_contract_version"] = _required_text(
            row.get("materiality_contract_version") or "unversioned",
            "materiality_contract_version",
        )
        v11_columns = (
            "content_materiality",
            "materiality_contract_version",
        )
    columns = base_columns + v11_columns
    saved = _insert_idempotent(
        conn,
        table="event_revision",
        identity_column="event_revision_id",
        columns=columns,
        values={column: row.get(column) for column in columns},
    )
    for field, default in (
        ("key_points_json", []),
        ("source_refs_json", []),
        ("price_reaction_json", {}),
    ):
        saved[field.removesuffix("_json")] = _json_load(saved.pop(field), default)
    return saved


def create_content_assessment(
    conn: sqlite3.Connection,
    item: Mapping[str, Any],
) -> dict[str, Any]:
    ensure_single_track_v3_schema(conn)
    row = dict(item)
    for field in (
        "content_assessment_id",
        "event_revision_id",
        "content_cutoff",
        "prompt_version",
        "schema_version",
        "validator_version",
        "contract_version",
        "content_eligible_at",
        "content_recovery_deadline_at",
        "created_at",
        "updated_at",
    ):
        row[field] = _required_text(row.get(field), field)
    row["content_evidence_digest"] = _require_digest(
        row.get("content_evidence_digest"), "content_evidence_digest"
    )
    row["model_digest"] = _require_digest(row.get("model_digest"), "model_digest")
    revision = _select_one(
        conn,
        """
        SELECT content_evidence_digest,content_cutoff
        FROM event_revision WHERE event_revision_id=?
        """,
        (row["event_revision_id"],),
    )
    if revision is None:
        raise ValueError("content assessment event revision does not exist")
    if (
        revision["content_evidence_digest"] != row["content_evidence_digest"]
        or revision["content_cutoff"] != row["content_cutoff"]
    ):
        raise ValueError("content assessment evidence does not match the sealed event revision")
    for field in (
        "content_cutoff",
        "content_eligible_at",
        "content_recovery_deadline_at",
        "created_at",
        "updated_at",
    ):
        _aware_timestamp(row[field], field)
    if _aware_timestamp(row["content_recovery_deadline_at"], "content_recovery_deadline_at") <= _aware_timestamp(
        row["content_eligible_at"], "content_eligible_at"
    ):
        raise ValueError("content recovery deadline must be after eligibility")
    key = build_content_assessment_key(
        event_revision_id=row["event_revision_id"],
        content_evidence_digest=row["content_evidence_digest"],
        content_cutoff=row["content_cutoff"],
        model_digest=row["model_digest"],
        prompt_version=row["prompt_version"],
        schema_version=row["schema_version"],
        validator_version=row["validator_version"],
        contract_version=row["contract_version"],
    )
    if row.get("content_assessment_key") not in (None, key):
        raise ValueError("content_assessment_key does not match its versioned identity")
    row.update(
        content_assessment_key=key,
        content_generation=int(row.get("content_generation") or 1),
        assessment_status="queued",
        state_version=0,
        promotion_epoch=0,
        active_attempt_id=None,
        next_attempt_at=None,
        attempt_count=0,
        model_repair_count=0,
        validation_state="pending",
        result_json=None,
        result_digest=None,
        terminal_transition_id=None,
        terminal_reason_code=None,
        last_failure_code=None,
        terminal_at=None,
        sealed_at=None,
    )
    columns = (
        "content_assessment_id",
        "content_assessment_key",
        "event_revision_id",
        "content_evidence_digest",
        "content_cutoff",
        "model_digest",
        "prompt_version",
        "schema_version",
        "validator_version",
        "contract_version",
        "content_generation",
        "content_eligible_at",
        "content_recovery_deadline_at",
        "assessment_status",
        "state_version",
        "promotion_epoch",
        "active_attempt_id",
        "next_attempt_at",
        "attempt_count",
        "model_repair_count",
        "validation_state",
        "result_json",
        "result_digest",
        "terminal_transition_id",
        "terminal_reason_code",
        "last_failure_code",
        "terminal_at",
        "sealed_at",
        "created_at",
        "updated_at",
    )
    return _insert_idempotent(
        conn,
        table="content_assessment",
        identity_column="content_assessment_key",
        columns=columns,
        values={column: row.get(column) for column in columns},
    )


def create_target_impact_assessment(
    conn: sqlite3.Connection,
    item: Mapping[str, Any],
) -> dict[str, Any]:
    """Create one per-target row only from an immutable validator-pass content result."""

    ensure_single_track_v3_schema(conn)
    row = dict(item)
    content_assessment_id = _required_text(
        row.get("content_assessment_id"), "content_assessment_id"
    )
    content = _select_one(
        conn,
        "SELECT * FROM content_assessment WHERE content_assessment_id=?",
        (content_assessment_id,),
    )
    if content is None:
        raise ValueError("target impact content assessment does not exist")
    if content["assessment_status"] == "superseded":
        raise ValueError("target impact cannot attach to superseded content")
    for field in (
        "target_impact_assessment_id",
        "target_entity_id",
        "target_trade_date",
        "forecast_target_id",
        "target_fact_snapshot_id",
        "prompt_version",
        "schema_version",
        "validator_version",
        "contract_version",
        "calibrator_version",
        "resolution_eligible_at",
        "prediction_issue_deadline_at",
        "created_at",
        "updated_at",
    ):
        row[field] = _required_text(row.get(field), field)
    row["target_fact_snapshot_digest"] = _require_digest(
        row.get("target_fact_snapshot_digest"), "target_fact_snapshot_digest"
    )
    row["target_context_digest"] = _require_digest(
        row.get("target_context_digest"), "target_context_digest"
    )
    row["model_digest"] = _require_digest(row.get("model_digest"), "model_digest")
    row["weight_version"] = _required_text(
        row.get("weight_version") or "candidate-unreleased", "weight_version"
    )
    row["target_generation"] = int(row.get("target_generation") or 1)
    immutable_columns = (
        "content_assessment_id",
        "target_entity_id",
        "target_trade_date",
        "forecast_target_id",
        "target_fact_snapshot_id",
        "target_fact_snapshot_digest",
        "target_context_digest",
        "model_digest",
        "prompt_version",
        "schema_version",
        "validator_version",
        "contract_version",
        "calibrator_version",
        "weight_version",
        "target_generation",
        "resolution_eligible_at",
        "prediction_issue_deadline_at",
    )
    existing_by_id = _select_one(
        conn,
        """
        SELECT * FROM target_impact_assessment
        WHERE target_impact_assessment_id=?
        """,
        (row["target_impact_assessment_id"],),
    )
    if existing_by_id is not None:
        expected_identity = {
            **row,
            "content_assessment_id": content_assessment_id,
        }
        if any(
            existing_by_id.get(column) != expected_identity.get(column)
            for column in immutable_columns
        ):
            raise ValueError(
                "target impact assessment ID already exists with different immutable content"
            )
        return existing_by_id
    eligible = _aware_timestamp(row["resolution_eligible_at"], "resolution_eligible_at")
    issue_deadline = _aware_timestamp(
        row["prediction_issue_deadline_at"], "prediction_issue_deadline_at"
    )
    if issue_deadline <= eligible:
        raise ValueError("prediction issue deadline must be after resolution eligibility")
    start_deadline = min(eligible + timedelta(minutes=10), issue_deadline)
    resolution_deadline = min(eligible + timedelta(minutes=20), issue_deadline)
    recovery_deadline = min(eligible + timedelta(minutes=60), issue_deadline)
    content_complete = bool(
        content["assessment_status"] == "complete"
        and content["validation_state"] == "pass"
        and content["sealed_at"] is not None
        and content["result_digest"] is not None
    )
    content_terminal = content["assessment_status"] == "failed_terminal"
    if content["assessment_status"] == "complete" and not content_complete:
        raise ValueError("complete content is not sealed validator-pass content")
    if content_complete:
        key = build_target_impact_key(
            content_assessment_key=content["content_assessment_key"],
            content_result_digest=content["result_digest"],
            target_entity_id=row["target_entity_id"],
            target_trade_date=row["target_trade_date"],
            forecast_target_id=row["forecast_target_id"],
            target_fact_snapshot_digest=row["target_fact_snapshot_digest"],
            target_context_digest=row["target_context_digest"],
            model_digest=row["model_digest"],
            prompt_version=row["prompt_version"],
            schema_version=row["schema_version"],
            validator_version=row["validator_version"],
            contract_version=row["contract_version"],
            calibrator_version=row["calibrator_version"],
            weight_version=row["weight_version"],
        )
    else:
        key = build_waiting_target_impact_key(
            content_assessment_key=content["content_assessment_key"],
            target_entity_id=row["target_entity_id"],
            target_trade_date=row["target_trade_date"],
            forecast_target_id=row["forecast_target_id"],
            target_fact_snapshot_digest=row["target_fact_snapshot_digest"],
            target_context_digest=row["target_context_digest"],
            model_digest=row["model_digest"],
            prompt_version=row["prompt_version"],
            schema_version=row["schema_version"],
            validator_version=row["validator_version"],
            contract_version=row["contract_version"],
            calibrator_version=row["calibrator_version"],
            weight_version=row["weight_version"],
        )
    if row.get("target_impact_key") not in (None, key):
        raise ValueError("target_impact_key does not match its versioned identity")
    row.update(
        target_impact_key=key,
        content_result_digest=content["result_digest"] if content_complete else None,
        event_revision_id=content["event_revision_id"],
        target_generation=row["target_generation"],
        resolution_start_deadline_at=_timestamp(start_deadline),
        resolution_deadline_at=_timestamp(resolution_deadline),
        target_recovery_deadline_at=_timestamp(recovery_deadline),
        assessment_status=(
            "queued" if content_complete else
            "blocked_dependency_terminal" if content_terminal else
            "waiting_for_content"
        ),
        decision_status="suppressed_unresolved" if content_terminal else "pending",
        state_version=0,
        promotion_epoch=0,
        active_attempt_id=None,
        next_attempt_at=None,
        target_attempt_count=0,
        model_repair_count=0,
        validation_state="pending",
        direction_probabilities_json=None,
        magnitude_probabilities_json=None,
        drivers_json="[]",
        counterevidence_json="[]",
        uncertainty_json="[]",
        evidence_ids_json="[]",
        priced_in_state="unknown",
        eligible_for_explanation=0,
        eligible_for_weight=0,
        calibration_state="unavailable",
        target_materiality="unknown_pending",
        target_direction="unknown",
        target_impact_magnitude="unknown_pending",
        regime_selection="suppressed_pending",
        target_relationship_type="unresolved",
        result_digest=None,
        terminal_reason_code="upstream_content_terminal" if content_terminal else None,
        upstream_terminal_reason_code=(
            content["terminal_reason_code"] if content_terminal else None
        ),
        terminal_at=content["terminal_at"] if content_terminal else None,
        sealed_at=None,
    )
    columns = (
        "target_impact_assessment_id",
        "target_impact_key",
        "content_assessment_id",
        "content_result_digest",
        "event_revision_id",
        "target_entity_id",
        "target_trade_date",
        "forecast_target_id",
        "target_fact_snapshot_id",
        "target_fact_snapshot_digest",
        "target_context_digest",
        "model_digest",
        "prompt_version",
        "schema_version",
        "validator_version",
        "contract_version",
        "calibrator_version",
        "weight_version",
        "target_generation",
        "resolution_eligible_at",
        "resolution_start_deadline_at",
        "resolution_deadline_at",
        "target_recovery_deadline_at",
        "prediction_issue_deadline_at",
        "assessment_status",
        "decision_status",
        "state_version",
        "promotion_epoch",
        "active_attempt_id",
        "next_attempt_at",
        "target_attempt_count",
        "model_repair_count",
        "validation_state",
        "direction_probabilities_json",
        "magnitude_probabilities_json",
        "drivers_json",
        "counterevidence_json",
        "uncertainty_json",
        "evidence_ids_json",
        "priced_in_state",
        "eligible_for_explanation",
        "eligible_for_weight",
        "calibration_state",
        "target_materiality",
        "target_direction",
        "target_impact_magnitude",
        "regime_selection",
        "target_relationship_type",
        "result_digest",
        "terminal_reason_code",
        "upstream_terminal_reason_code",
        "terminal_at",
        "sealed_at",
        "created_at",
        "updated_at",
    )
    return _insert_idempotent(
        conn,
        table="target_impact_assessment",
        identity_column="target_impact_key",
        columns=columns,
        values={column: row.get(column) for column in columns},
    )



_CONTENT_STATE_EXPORTS = frozenset(
    {
        "begin_content_model_dispatch",
        "seal_content_assessment_result",
        "record_content_attempt_failure",
        "terminalize_expired_content_assessment",
        "reconcile_waiting_on_terminal_content",
    }
)
_TARGET_STATE_EXPORTS = frozenset(
    {
        "begin_target_model_dispatch",
        "seal_target_impact_assessment_result",
        "record_target_attempt_failure",
        "terminalize_expired_target_assessment",
    }
)

__all__ = (
    "build_content_assessment_key",
    "build_target_impact_key",
    "build_waiting_target_impact_key",
    "create_event_cluster",
    "create_event_revision",
    "create_content_assessment",
    "create_target_impact_assessment",
    *_CONTENT_STATE_EXPORTS,
    *_TARGET_STATE_EXPORTS,
)


def __getattr__(name: str) -> Any:
    """Preserve the pre-split repository import surface without eager cycles."""

    if name in _CONTENT_STATE_EXPORTS:
        from repository import (
            single_track_v3_content_assessment_state_repository as state_repository,
        )
    elif name in _TARGET_STATE_EXPORTS:
        from repository import (
            single_track_v3_target_assessment_state_repository as state_repository,
        )
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(state_repository, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
