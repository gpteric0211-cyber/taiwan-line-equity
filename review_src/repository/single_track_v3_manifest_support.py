from __future__ import annotations

import hashlib
import itertools
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from analysis.target_label_contract_v1 import (
    OFFICIAL_ADJUSTMENT_BASIS,
    TARGET_CLASSES,
    TARGET_LABEL_CONTRACT_VERSION,
)
from analysis import target_label_contract_v1 as target_label_module
from evaluation.statistical_release_gate_v1 import (
    BOOTSTRAP_BLOCK_DAYS,
    BOOTSTRAP_ITERATIONS,
    BOOTSTRAP_SEED,
    GATE_SPEC,
    GATE_SPEC_HASH,
    STATISTICAL_RELEASE_GATE_VERSION,
)
from evaluation import statistical_release_gate_v1 as statistical_gate_module


PREDICTION_COMPLETION_STATES = {
    "completed",
    "abstained",
    "omitted",
    "failed",
    "unavailable",
}
VALID_REGIMES = {"normal", "material_event"}
_SAVEPOINT_SEQUENCE = itertools.count(1)


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("manifest payload must be finite JSON") from exc


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def current_evaluator_source_digest() -> str:
    source_path = Path(required_text(statistical_gate_module.__file__, "evaluator.__file__"))
    return hashlib.sha256(source_path.read_bytes()).hexdigest()


def current_target_label_source_digest() -> str:
    source_path = Path(required_text(target_label_module.__file__, "target_label.__file__"))
    return hashlib.sha256(source_path.read_bytes()).hexdigest()


def json_object(value: Any, field: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"{field} must contain a JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{field} must contain a JSON object")
    canonical_json(parsed)
    return parsed


def json_value(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value))
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def canonical_date(value: Any, field: str) -> str:
    text = required_text(value, field)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 date") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{field} must be a canonical ISO-8601 date")
    return text


def aware_timestamp(value: Any, field: str) -> datetime:
    text = required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def sha256_digest(value: Any, field: str) -> str:
    text = required_text(value, field).casefold()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return text


def finite_probability(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite probability")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite probability") from exc
    if not math.isfinite(number) or number < 0 or number > 1:
        raise ValueError(f"{field} must be between zero and one")
    return number


def finite_positive_or_none(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite positive number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field} must be a finite positive number")
    return number


def select_one(
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
    columns = [str(item[0]) for item in cursor.description or ()]
    return dict(zip(columns, source, strict=True))


def select_rows(
    conn: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    cursor = conn.execute(query, parameters)
    columns = [str(item[0]) for item in cursor.description or ()]
    result = []
    for source in cursor.fetchall():
        if isinstance(source, sqlite3.Row):
            result.append(dict(source))
        else:
            result.append(dict(zip(columns, source, strict=True)))
    return result


def normalize_member_keys(
    members: Iterable[Mapping[str, Any]],
) -> list[dict[str, str]]:
    normalized = []
    for source in members:
        if not isinstance(source, Mapping):
            raise ValueError("holdout members must be objects")
        sample_id = required_text(source.get("sample_id"), "sample_id")
        target_key = required_text(source.get("target_key"), "target_key")
        if target_key not in TARGET_CLASSES:
            raise ValueError("target_key is invalid")
        normalized.append({"sample_id": sample_id, "target_key": target_key})
    normalized.sort(key=lambda item: (item["sample_id"], item["target_key"]))
    identities = [(item["sample_id"], item["target_key"]) for item in normalized]
    if not identities:
        raise ValueError("holdout partition must contain at least one sample target")
    if len(set(identities)) != len(identities):
        raise ValueError("holdout partition contains duplicate sample targets")
    if len(identities) > 100_000:
        raise ValueError("holdout partition exceeds its member limit")
    return normalized


def gate_manifest_row(conn: sqlite3.Connection, gate_manifest_id: str) -> dict[str, Any]:
    row = select_one(
        conn,
        "SELECT * FROM statistical_gate_manifest WHERE gate_manifest_id=?",
        (str(gate_manifest_id),),
    )
    if row is None:
        raise ValueError("gate_manifest_id does not exist")
    return row


def gate_member_keys(
    conn: sqlite3.Connection,
    gate_manifest_id: str,
) -> list[dict[str, str]]:
    return [
        {"sample_id": str(row["sample_id"]), "target_key": str(row["target_key"])}
        for row in select_rows(
            conn,
            """
            SELECT sample_id,target_key FROM statistical_gate_holdout_member
            WHERE gate_manifest_id=? ORDER BY sample_id,target_key
            """,
            (str(gate_manifest_id),),
        )
    ]


def validate_supplied_digest(value: Any, expected: str, field: str) -> str:
    if value is None:
        return expected
    if sha256_digest(value, field) != expected:
        raise ValueError(f"{field} does not match canonical manifest content")
    return expected


@contextmanager
def manifest_savepoint(conn: sqlite3.Connection, label: str) -> Iterator[None]:
    suffix = next(_SAVEPOINT_SEQUENCE)
    name = f"stv3_{label}_{suffix}"
    conn.execute(f'SAVEPOINT "{name}"')
    try:
        yield
    except Exception:
        conn.execute(f'ROLLBACK TO SAVEPOINT "{name}"')
        conn.execute(f'RELEASE SAVEPOINT "{name}"')
        raise
    else:
        conn.execute(f'RELEASE SAVEPOINT "{name}"')


__all__ = [
    "BOOTSTRAP_BLOCK_DAYS",
    "BOOTSTRAP_ITERATIONS",
    "BOOTSTRAP_SEED",
    "GATE_SPEC",
    "GATE_SPEC_HASH",
    "OFFICIAL_ADJUSTMENT_BASIS",
    "PREDICTION_COMPLETION_STATES",
    "STATISTICAL_RELEASE_GATE_VERSION",
    "TARGET_CLASSES",
    "TARGET_LABEL_CONTRACT_VERSION",
    "VALID_REGIMES",
    "aware_timestamp",
    "canonical_date",
    "canonical_digest",
    "canonical_json",
    "current_evaluator_source_digest",
    "current_target_label_source_digest",
    "finite_positive_or_none",
    "finite_probability",
    "gate_manifest_row",
    "gate_member_keys",
    "json_object",
    "json_value",
    "manifest_savepoint",
    "normalize_member_keys",
    "required_text",
    "select_one",
    "select_rows",
    "sha256_digest",
    "validate_supplied_digest",
]
