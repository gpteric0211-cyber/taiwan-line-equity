from __future__ import annotations

"""Append-only backend provenance and point-in-time observation storage."""

import hashlib
import json
import sqlite3
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from core.provenance_contract import (
    DATASET_AVAILABILITY_CONTRACTS,
    AvailabilityPolicy,
    compute_usable_from,
)


TPE = ZoneInfo("Asia/Taipei")
UTC = timezone.utc


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("provenance timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="seconds")


def ensure_provenance_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS data_source_contract (
            source_key TEXT PRIMARY KEY,
            source_label TEXT NOT NULL,
            event_semantics TEXT NOT NULL,
            required_maturity_stage TEXT NOT NULL,
            safety_delay_seconds INTEGER NOT NULL,
            fallback_policy TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS data_observation_version (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_key TEXT NOT NULL,
            natural_key TEXT NOT NULL,
            event_at TEXT NOT NULL,
            source_key TEXT NOT NULL,
            source_published_at TEXT,
            first_seen_at TEXT NOT NULL,
            validation_passed_at TEXT NOT NULL,
            usable_from TEXT NOT NULL,
            revised_at TEXT,
            revision_no INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            quality_status TEXT NOT NULL,
            maturity_stage TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY(source_key) REFERENCES data_source_contract(source_key),
            UNIQUE(dataset_key,natural_key,revision_no),
            UNIQUE(dataset_key,natural_key,content_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_observation_pit
            ON data_observation_version(dataset_key,natural_key,usable_from,revision_no);
        CREATE INDEX IF NOT EXISTS idx_observation_source_event
            ON data_observation_version(source_key,event_at);
        """
    )
    now_text = _utc_text(datetime.now(UTC))
    conn.executemany(
        """
        INSERT INTO data_source_contract(
            source_key,source_label,event_semantics,required_maturity_stage,
            safety_delay_seconds,fallback_policy,schema_version,active,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_key) DO UPDATE SET
            event_semantics=excluded.event_semantics,
            required_maturity_stage=excluded.required_maturity_stage,
            safety_delay_seconds=excluded.safety_delay_seconds,
            fallback_policy=excluded.fallback_policy,
            schema_version=excluded.schema_version,
            active=1,
            updated_at=excluded.updated_at
        """,
        [
            (
                f"policy:{dataset_key}",
                f"{dataset_key} availability policy",
                contract.event_semantics,
                contract.policy.required_maturity_stage,
                contract.policy.safety_delay_seconds,
                contract.policy.fallback_policy,
                contract.schema_version,
                1,
                now_text,
                now_text,
            )
            for dataset_key, contract in DATASET_AVAILABILITY_CONTRACTS.items()
        ],
    )


def _canonical_payload(payload: dict[str, Any]) -> tuple[str, str]:
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return payload_json, hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def record_validated_daily_ohlcv(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    *,
    observed_at: datetime | None = None,
) -> bool:
    """Append one accepted daily OHLCV version without rewriting prior evidence."""

    observed = (observed_at or datetime.now(UTC)).astimezone(UTC)
    trade_date = datetime.fromisoformat(str(row["date"])).date()
    event_at = datetime.combine(trade_date, time(13, 30), tzinfo=TPE).astimezone(UTC)
    source_label = str(row.get("source") or "UNKNOWN").strip() or "UNKNOWN"
    source_key = f"daily_ohlcv:{source_label}"
    source_quality = str(row.get("source_quality") or "unknown").strip().lower()
    # This function is called only after the centralized OHLCV validation and
    # source-priority gate accepts the row. Provider tier remains separate
    # provenance metadata and must not be confused with schema/value validity.
    quality_status = "validated"
    policy = AvailabilityPolicy(required_maturity_stage="final", safety_delay_seconds=120)
    usable_from = compute_usable_from(
        first_seen_at=observed,
        validation_passed_at=observed,
        quality_status=quality_status,
        maturity_stage="final",
        schema_valid=True,
        policy=policy,
    )
    if usable_from is None:
        return False

    now_text = _utc_text(observed)
    conn.execute(
        """
        INSERT INTO data_source_contract(
            source_key,source_label,event_semantics,required_maturity_stage,
            safety_delay_seconds,fallback_policy,schema_version,active,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_key) DO UPDATE SET
            source_label=excluded.source_label,
            event_semantics=excluded.event_semantics,
            required_maturity_stage=excluded.required_maturity_stage,
            safety_delay_seconds=excluded.safety_delay_seconds,
            fallback_policy=excluded.fallback_policy,
            schema_version=excluded.schema_version,
            active=1,
            updated_at=excluded.updated_at
        """,
        (
            source_key,
            source_label,
            "completed regular-session OHLCV for the named Taiwan trading date; event_at is 13:30 Asia/Taipei",
            policy.required_maturity_stage,
            policy.safety_delay_seconds,
            policy.fallback_policy,
            "daily-ohlcv-v1",
            1,
            now_text,
            now_text,
        ),
    )
    payload = {
        key: row.get(key)
        for key in ("date", "code", "open", "high", "low", "close", "volume", "amount", "volume_unit", "market", "source")
    }
    payload["input_source_quality"] = source_quality
    payload_json, content_hash = _canonical_payload(payload)
    natural_key = f"{row['date']}:{row['code']}"
    duplicate = conn.execute(
        """
        SELECT 1 FROM data_observation_version
        WHERE dataset_key='daily_ohlcv' AND natural_key=? AND content_hash=?
        """,
        (natural_key, content_hash),
    ).fetchone()
    if duplicate:
        return False
    latest = conn.execute(
        """
        SELECT MAX(revision_no) FROM data_observation_version
        WHERE dataset_key='daily_ohlcv' AND natural_key=?
        """,
        (natural_key,),
    ).fetchone()
    revision_no = int((latest[0] if latest else 0) or 0) + 1
    conn.execute(
        """
        INSERT INTO data_observation_version(
            dataset_key,natural_key,event_at,source_key,source_published_at,
            first_seen_at,validation_passed_at,usable_from,revised_at,revision_no,
            content_hash,schema_version,quality_status,maturity_stage,payload_json,recorded_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "daily_ohlcv",
            natural_key,
            _utc_text(event_at),
            source_key,
            None,
            now_text,
            now_text,
            _utc_text(usable_from),
            now_text if revision_no > 1 else None,
            revision_no,
            content_hash,
            "daily-ohlcv-v1",
            "validated",
            "final",
            payload_json,
            now_text,
        ),
    )
    return True


def record_validated_institution_activity_version(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    *,
    observed_at: datetime | None = None,
    ensure_schema: bool = True,
) -> bool:
    """Append one official three-institution daily observation version.

    Historical imports are intentionally usable only after this system actually
    observed and validated them.  Their trading date must never be substituted
    for ``first_seen_at`` during walk-forward evaluation.
    """

    if ensure_schema:
        ensure_provenance_schema(conn)
    observed = (observed_at or datetime.now(UTC)).astimezone(UTC)
    contract = DATASET_AVAILABILITY_CONTRACTS["institution_daily"]
    usable_from = compute_usable_from(
        first_seen_at=observed,
        validation_passed_at=observed,
        quality_status="validated",
        maturity_stage="final",
        schema_valid=True,
        policy=contract.policy,
    )
    if usable_from is None:
        return False
    trade_date = datetime.fromisoformat(str(row["date"])).date()
    event_at = datetime.combine(trade_date, time(13, 30), tzinfo=TPE).astimezone(UTC)
    source_label = str(row.get("source") or "").strip()
    if not source_label:
        return False
    now_text = _utc_text(observed)
    source_key = f"institution_daily:{source_label}"
    conn.execute(
        """
        INSERT INTO data_source_contract(
            source_key,source_label,event_semantics,required_maturity_stage,
            safety_delay_seconds,fallback_policy,schema_version,active,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_key) DO UPDATE SET
            source_label=excluded.source_label,
            event_semantics=excluded.event_semantics,
            required_maturity_stage=excluded.required_maturity_stage,
            safety_delay_seconds=excluded.safety_delay_seconds,
            fallback_policy=excluded.fallback_policy,
            schema_version=excluded.schema_version,
            active=1,
            updated_at=excluded.updated_at
        """,
        (
            source_key,
            source_label,
            contract.event_semantics,
            contract.policy.required_maturity_stage,
            contract.policy.safety_delay_seconds,
            contract.policy.fallback_policy,
            contract.schema_version,
            1,
            now_text,
            now_text,
        ),
    )
    payload = {
        field: row.get(field)
        for field in (
            "date",
            "code",
            "market",
            "foreign_buy",
            "foreign_sell",
            "foreign_net",
            "trust_buy",
            "trust_sell",
            "trust_net",
            "dealer_buy",
            "dealer_sell",
            "dealer_net",
        )
    }
    payload["source"] = source_label
    payload["source_quality"] = "official"
    payload_json, content_hash = _canonical_payload(payload)
    natural_key = f"{row['date']}:{row['code']}"
    duplicate = conn.execute(
        """
        SELECT 1 FROM data_observation_version
        WHERE dataset_key='institution_daily' AND natural_key=? AND content_hash=?
        """,
        (natural_key, content_hash),
    ).fetchone()
    if duplicate:
        return False
    latest = conn.execute(
        """
        SELECT MAX(revision_no) FROM data_observation_version
        WHERE dataset_key='institution_daily' AND natural_key=?
        """,
        (natural_key,),
    ).fetchone()
    revision_no = int((latest[0] if latest else 0) or 0) + 1
    conn.execute(
        """
        INSERT INTO data_observation_version(
            dataset_key,natural_key,event_at,source_key,source_published_at,
            first_seen_at,validation_passed_at,usable_from,revised_at,revision_no,
            content_hash,schema_version,quality_status,maturity_stage,payload_json,recorded_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "institution_daily",
            natural_key,
            _utc_text(event_at),
            source_key,
            None,
            now_text,
            now_text,
            _utc_text(usable_from),
            now_text if revision_no > 1 else None,
            revision_no,
            content_hash,
            contract.schema_version,
            "validated",
            "final",
            payload_json,
            now_text,
        ),
    )
    return True


def record_validated_credit_balance_versions(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    *,
    observed_at: datetime | None = None,
) -> int:
    """Append point-in-time margin and lending versions for one accepted row."""

    ensure_provenance_schema(conn)
    observed = (observed_at or datetime.now(UTC)).astimezone(UTC)
    trade_date = datetime.fromisoformat(str(row["trade_date"])).date()
    event_at = datetime.combine(trade_date, time(13, 30), tzinfo=TPE).astimezone(UTC)
    recorded = 0
    definitions = (
        (
            "margin_daily",
            "margin_source",
            (
                "trade_date", "code", "market",
                "margin_prev_balance_lots", "margin_buy_lots", "margin_sell_lots",
                "margin_cash_repayment_lots", "margin_balance_lots", "margin_delta_lots",
                "margin_utilization_pct", "margin_utilization_method", "margin_limit_lots",
                "short_prev_balance_lots", "short_sell_lots", "short_buy_lots",
                "short_stock_repayment_lots", "short_balance_lots", "short_delta_lots",
                "short_utilization_pct", "short_utilization_method", "short_limit_lots",
            ),
        ),
        (
            "lending_daily",
            "lending_source",
            (
                "trade_date", "code", "market",
                "sbl_prev_balance_shares", "sbl_sell_shares", "sbl_return_shares",
                "sbl_adjust_shares", "sbl_balance_shares", "sbl_delta_shares",
            ),
        ),
    )
    for dataset_key, source_field, payload_fields in definitions:
        source_label = str(row.get(source_field) or "").strip()
        value_fields = payload_fields[3:]
        if not source_label or all(row.get(field) is None for field in value_fields):
            continue
        contract = DATASET_AVAILABILITY_CONTRACTS[dataset_key]
        usable_from = compute_usable_from(
            first_seen_at=observed,
            validation_passed_at=observed,
            quality_status="validated",
            maturity_stage="final",
            schema_valid=True,
            policy=contract.policy,
        )
        if usable_from is None:
            continue
        now_text = _utc_text(observed)
        source_key = f"{dataset_key}:{source_label}"
        conn.execute(
            """
            INSERT INTO data_source_contract(
                source_key,source_label,event_semantics,required_maturity_stage,
                safety_delay_seconds,fallback_policy,schema_version,active,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_key) DO UPDATE SET
                source_label=excluded.source_label,
                event_semantics=excluded.event_semantics,
                required_maturity_stage=excluded.required_maturity_stage,
                safety_delay_seconds=excluded.safety_delay_seconds,
                fallback_policy=excluded.fallback_policy,
                schema_version=excluded.schema_version,
                active=1,
                updated_at=excluded.updated_at
            """,
            (
                source_key,
                source_label,
                contract.event_semantics,
                contract.policy.required_maturity_stage,
                contract.policy.safety_delay_seconds,
                contract.policy.fallback_policy,
                contract.schema_version,
                1,
                now_text,
                now_text,
            ),
        )
        payload = {field: row.get(field) for field in payload_fields}
        payload["source"] = source_label
        payload["source_quality"] = "official"
        payload["units"] = (
            {
                "margin_financing": "lots",
                "exchange_short_selling": "lots",
                "margin_utilization": "percent",
                "short_utilization": "percent",
            }
            if dataset_key == "margin_daily"
            else {"securities_borrowing_sbl": "shares"}
        )
        payload["candidate_contribution"] = 0.0
        payload["referee_eligible"] = False
        payload["next_day_outlook_eligible"] = False
        payload_json, content_hash = _canonical_payload(payload)
        natural_key = f"{row['trade_date']}:{row['code']}"
        duplicate = conn.execute(
            """
            SELECT 1 FROM data_observation_version
            WHERE dataset_key=? AND natural_key=? AND content_hash=?
            """,
            (dataset_key, natural_key, content_hash),
        ).fetchone()
        if duplicate:
            continue
        latest = conn.execute(
            """
            SELECT MAX(revision_no) FROM data_observation_version
            WHERE dataset_key=? AND natural_key=?
            """,
            (dataset_key, natural_key),
        ).fetchone()
        revision_no = int((latest[0] if latest else 0) or 0) + 1
        conn.execute(
            """
            INSERT INTO data_observation_version(
                dataset_key,natural_key,event_at,source_key,source_published_at,
                first_seen_at,validation_passed_at,usable_from,revised_at,revision_no,
                content_hash,schema_version,quality_status,maturity_stage,payload_json,recorded_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                dataset_key,
                natural_key,
                _utc_text(event_at),
                source_key,
                None,
                now_text,
                now_text,
                _utc_text(usable_from),
                now_text if revision_no > 1 else None,
                revision_no,
                content_hash,
                contract.schema_version,
                "validated",
                "final",
                payload_json,
                now_text,
            ),
        )
        recorded += 1
    return recorded
