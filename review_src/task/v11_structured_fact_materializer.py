from __future__ import annotations

"""Stage 2 V11 candidate-only structured fact materializers.

These background functions consume immutable source observations. Request
paths read the resulting facts and never compute or persist candidate scores.
"""

import hashlib
import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from core.config import TDCC_HOLDING_DISTRIBUTION_CSV_URL
from core.cost_source_registry import (
    CANONICAL_COST_FORMULA_VERSION,
    CANONICAL_COST_TYPES,
)
from core.market_calendar_cache import taiwan_market_day_status
from core.provenance_contract import (
    DATASET_AVAILABILITY_CONTRACTS,
    compute_usable_from,
)
from core.provenance_schema import ensure_provenance_schema
from repository.single_track_v3_repository import upsert_evidence_facts
from services.tdcc_equity_concentration_service import (
    TDCC_SOURCE,
    compute_equity_concentration_from_distribution,
    parse_holding_level_bounds,
)


CREDIT_FACT_VERSION = "ChipAnalysisContractV1:official-credit-raw-v1"
CREDIT_DATASETS = ("margin_daily", "lending_daily")
TDCC_DATASET_KEY = "holding_distribution_weekly"
TDCC_REVISION_VERSION = "TDCCSnapshotRevisionV1"
TDCC_SOURCE_POLICY_VERSION = "TDCCFreshnessPolicyV1-candidate.1"
COST_FACT_VERSION = "ChipAnalysisContractV1:corporate-action-safe-cost-v1"
TPE = ZoneInfo("Asia/Taipei")
MARGIN_FACT_UNITS = {
    "margin_prev_balance_lots": "lots",
    "margin_buy_lots": "lots",
    "margin_sell_lots": "lots",
    "margin_cash_repayment_lots": "lots",
    "margin_balance_lots": "lots",
    "margin_delta_lots": "lots",
    "margin_utilization_pct": "percent",
    "margin_utilization_method": None,
    "margin_limit_lots": "lots",
    "short_prev_balance_lots": "lots",
    "short_sell_lots": "lots",
    "short_buy_lots": "lots",
    "short_stock_repayment_lots": "lots",
    "short_balance_lots": "lots",
    "short_delta_lots": "lots",
    "short_utilization_pct": "percent",
    "short_utilization_method": None,
    "short_limit_lots": "lots",
}
SBL_FACT_UNITS = {
    "sbl_prev_balance_shares": "shares",
    "sbl_sell_shares": "shares",
    "sbl_return_shares": "shares",
    "sbl_adjust_shares": "shares",
    "sbl_balance_shares": "shares",
    "sbl_delta_shares": "shares",
}


def _json(value: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _aware(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def _utc_text(value: datetime) -> str:
    return _aware(value, field="timestamp").astimezone(timezone.utc).isoformat(timespec="seconds")


def _tpe_text(value: datetime) -> str:
    return _aware(value, field="timestamp").astimezone(TPE).isoformat(timespec="seconds")


def _dimension(field: str) -> str:
    if field.startswith("margin_"):
        return "margin_financing"
    if field.startswith("short_"):
        return "exchange_short_selling"
    return "securities_borrowing_sbl"


def materialize_credit_balance_candidate_facts(
    conn: sqlite3.Connection,
    *,
    stock_code: str | None = None,
    trade_date: str | None = None,
) -> dict[str, Any]:
    """Materialize immutable, unit-correct official credit facts at weight 0."""

    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='data_observation_version'"
    ).fetchone()
    if not table:
        return {
            "status": "unavailable",
            "source_observations": 0,
            "facts_written": 0,
            "candidate_contribution": 0.0,
        }

    predicates = ["dataset_key IN ('margin_daily','lending_daily')"]
    parameters: list[Any] = []
    if trade_date:
        predicates.append("natural_key LIKE ?")
        parameters.append(f"{trade_date}:%")
    if stock_code:
        predicates.append("natural_key LIKE ?")
        parameters.append(f"%:{str(stock_code).zfill(4)}")
    rows = conn.execute(
        f"""
        SELECT dataset_key,natural_key,event_at,first_seen_at,usable_from,
               revision_no,content_hash,payload_json,recorded_at
        FROM data_observation_version
        WHERE {' AND '.join(predicates)}
          AND quality_status='validated'
          AND maturity_stage IN ('final','revised')
        ORDER BY dataset_key,natural_key,revision_no
        """,
        parameters,
    ).fetchall()

    facts: list[dict[str, Any]] = []
    invalid_observations = 0
    for row in rows:
        dataset_key = str(row[0])
        payload = _json(row[7])
        if not isinstance(payload, dict):
            invalid_observations += 1
            continue
        source = str(payload.get("source") or "").strip()
        code = str(payload.get("code") or "").strip().zfill(4)
        source_trade_date = str(payload.get("trade_date") or "").strip()
        if not source or len(code) != 4 or not code.isdigit() or not source_trade_date:
            invalid_observations += 1
            continue
        field_units = MARGIN_FACT_UNITS if dataset_key == "margin_daily" else SBL_FACT_UNITS
        for field, unit in field_units.items():
            value = payload.get(field)
            if value is None:
                continue
            dimension = _dimension(field)
            content_hash = str(row[6])
            revision_no = int(row[5])
            facts.append(
                {
                    "fact_id": (
                        f"v11-credit:{dataset_key}:{source_trade_date}:{code}:"
                        f"r{revision_no}:{field}"
                    ),
                    "entity": code,
                    "field": field,
                    "value": value,
                    "unit_currency": unit,
                    "unit": unit,
                    "period": "daily_post_settlement",
                    "trade_date": source_trade_date,
                    "as_of": source_trade_date,
                    "available_at": str(row[4]),
                    "first_seen_at": str(row[3]),
                    "usable_from": str(row[4]),
                    "source_revision_id": content_hash,
                    "source_market_timestamp": str(row[2]),
                    "session": "post_settlement",
                    "adjustment_basis": "raw_official_components_no_cross_unit_aggregation",
                    "authority_tier": "canonical_official",
                    "quality": "official_validated",
                    "availability_reason": None,
                    "use_scope": "candidate_shadow_only",
                    "snapshot_id": f"source-observation:{content_hash}",
                    "provenance": {
                        "dataset_key": dataset_key,
                        "natural_key": str(row[1]),
                        "observation_revision_no": revision_no,
                        "source": source,
                        "dimension": dimension,
                        "candidate_contribution": 0.0,
                        "normalization_state": "not_calibrated",
                        "referee_eligible": False,
                        "next_day_outlook_eligible": False,
                        "missing_weight_redistribution": False,
                        "legacy_margin_daily_read": False,
                    },
                    "formula_source_version": CREDIT_FACT_VERSION,
                    "recorded_at": str(row[8]),
                }
            )

    existing_ids: set[str] = set()
    if facts:
        fact_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='canonical_evidence_fact'"
        ).fetchone()
        if fact_table:
            requested_ids = [str(fact["fact_id"]) for fact in facts]
            for start in range(0, len(requested_ids), 500):
                batch = requested_ids[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                existing_ids.update(
                    str(item[0])
                    for item in conn.execute(
                        f"SELECT fact_id FROM canonical_evidence_fact WHERE fact_id IN ({placeholders})",
                        batch,
                    ).fetchall()
                )
    new_facts = [fact for fact in facts if fact["fact_id"] not in existing_ids]
    written = upsert_evidence_facts(conn, new_facts) if new_facts else 0
    return {
        "status": "ok" if rows and not invalid_observations else ("partial" if rows else "unavailable"),
        "source_observations": len(rows),
        "invalid_observations": invalid_observations,
        "facts_built": len(facts),
        "facts_written": written,
        "candidate_contribution": 0.0,
        "referee_eligible": False,
        "next_day_outlook_eligible": False,
        "source_contract": "append_only_data_observation_version",
        "legacy_margin_daily_read": False,
    }


def read_credit_balance_candidate_fact_bundle(
    conn: sqlite3.Connection,
    *,
    stock_code: str,
    target_date: str,
    decision_at: datetime,
) -> dict[str, Any]:
    """Read precomputed candidate facts without schema ensure or any write."""

    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise ValueError("decision_at must be timezone-aware")
    cutoff = decision_at.astimezone(timezone.utc)
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='canonical_evidence_fact'"
    ).fetchone()
    if not table:
        return _credit_bundle_result(stock_code, target_date, [])
    rows = conn.execute(
        """
        SELECT fact_id,field,value_json,unit,trade_date,available_at,
               source_revision_id,provenance_json
        FROM canonical_evidence_fact
        WHERE entity=? AND trade_date<=? AND formula_source_version=?
        ORDER BY trade_date DESC,available_at DESC,fact_id
        """,
        (str(stock_code).zfill(4), str(target_date), CREDIT_FACT_VERSION),
    ).fetchall()
    visible: list[dict[str, Any]] = []
    for row in rows:
        try:
            available = datetime.fromisoformat(str(row[5]).replace("Z", "+00:00"))
            if available.tzinfo is None or available.astimezone(timezone.utc) > cutoff:
                continue
        except ValueError:
            continue
        provenance = _json(row[7])
        visible.append(
            {
                "fact_id": str(row[0]),
                "field": str(row[1]),
                "value": _json(row[2]),
                "unit": row[3],
                "trade_date": str(row[4]),
                "available_at": str(row[5]),
                "source_revision_id": str(row[6]),
                "provenance": provenance if isinstance(provenance, dict) else {},
            }
        )
    if not visible:
        return _credit_bundle_result(stock_code, target_date, [])
    newest_date = max(item["trade_date"] for item in visible)
    newest = [item for item in visible if item["trade_date"] == newest_date]
    latest_by_field: dict[str, dict[str, Any]] = {}
    for item in newest:
        latest_by_field.setdefault(item["field"], item)
    return _credit_bundle_result(stock_code, target_date, list(latest_by_field.values()))


def _credit_bundle_result(
    stock_code: str,
    target_date: str,
    facts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "contract_version": CREDIT_FACT_VERSION,
        "stock_code": str(stock_code).zfill(4),
        "target_date": str(target_date),
        "status": "ok" if facts else "unavailable",
        "facts": sorted(facts, key=lambda item: item["field"]),
        "candidate_contribution": 0.0,
        "referee_eligible": False,
        "next_day_outlook_eligible": False,
        "request_time_computation": False,
        "request_time_writes": 0,
        "legacy_margin_daily_read": False,
    }


def _tdcc_expected_publication(
    source_week_date: str,
    *,
    weeks_ahead: int,
) -> datetime:
    source_date = date.fromisoformat(str(source_week_date))
    next_week_anchor = source_date + timedelta(days=7 * int(weeks_ahead))
    monday = next_week_anchor - timedelta(days=next_week_anchor.weekday())
    for weekday in range(4, -1, -1):
        candidate = monday + timedelta(days=weekday)
        status = taiwan_market_day_status(candidate)
        if not status.get("verified"):
            raise ValueError(
                "official calendar unavailable for TDCC expected week "
                f"{candidate.isocalendar()[:2]}"
            )
        if status.get("is_trading_day"):
            return datetime.combine(candidate, time(23, 59, 59), tzinfo=TPE)
    raise ValueError("TDCC expected week contains no verified trading session")


def _tdcc_rows_for_date(
    conn: sqlite3.Connection,
    *,
    stock_code: str,
    source_week_date: str,
) -> list[dict[str, Any]]:
    cursor = conn.execute(
        """
        SELECT date,code,level,holders,shares,percent,source
        FROM tdcc_holding_distribution
        WHERE code=? AND date=?
        ORDER BY CAST(level AS INTEGER),level
        """,
        (str(stock_code).zfill(4), str(source_week_date)),
    )
    columns = [str(item[0]) for item in cursor.description or ()]
    rows: list[dict[str, Any]] = []
    for source in cursor.fetchall():
        row = (
            dict(source)
            if isinstance(source, sqlite3.Row)
            else dict(zip(columns, source, strict=True))
        )
        level_no, lower, upper = parse_holding_level_bounds(row.get("level"))
        rows.append(
            {
                **row,
                "level_no": level_no,
                "lower_shares": lower,
                "upper_shares": upper,
            }
        )
    return rows


def _tdcc_previous_rows(
    conn: sqlite3.Connection,
    *,
    stock_code: str,
    source_week_date: str,
) -> list[dict[str, Any]]:
    dates = [
        str(row[0])
        for row in conn.execute(
            """
            SELECT DISTINCT date FROM tdcc_holding_distribution
            WHERE code=? AND date<=? ORDER BY date
            """,
            (str(stock_code).zfill(4), str(source_week_date)),
        ).fetchall()
    ]
    if len(dates) < 5:
        return []
    return _tdcc_rows_for_date(
        conn,
        stock_code=stock_code,
        source_week_date=dates[-5],
    )


def _tdcc_freshness(
    *,
    decision_at: datetime,
    usable_from: datetime,
    grace_deadline: datetime,
    hard_stale_at: datetime,
) -> str:
    decision = _aware(decision_at, field="decision_at").astimezone(TPE)
    if decision < _aware(usable_from, field="usable_from").astimezone(TPE):
        return "unavailable"
    if decision <= _aware(grace_deadline, field="grace_deadline").astimezone(TPE):
        return "ok"
    if decision < _aware(hard_stale_at, field="hard_stale_at").astimezone(TPE):
        return "source_delayed"
    return "stale"


def _record_tdcc_candidate_revision(
    conn: sqlite3.Connection,
    *,
    rows: list[dict[str, Any]],
    previous_rows: list[dict[str, Any]],
    observed_at: datetime,
) -> dict[str, Any]:
    observed = _aware(observed_at, field="observed_at").astimezone(timezone.utc)
    dates = {str(row.get("date") or "") for row in rows}
    codes = {str(row.get("code") or "").zfill(4) for row in rows}
    sources = {str(row.get("source") or "") for row in rows}
    if len(dates) != 1 or len(codes) != 1:
        raise ValueError("TDCC revision requires exactly one code and source week date")
    if sources != {TDCC_SOURCE}:
        raise ValueError("TDCC candidate revision requires only the official TDCC source")
    source_week_date = next(iter(dates))
    stock_code = next(iter(codes))
    if date.fromisoformat(source_week_date) > observed.astimezone(TPE).date():
        raise ValueError("TDCC source week date cannot be after backend observation")
    prior_summary = (
        compute_equity_concentration_from_distribution(previous_rows)
        if previous_rows
        else None
    )
    summary = compute_equity_concentration_from_distribution(rows, prior_summary)
    if summary.get("quality") != "ok":
        raise ValueError("TDCC distribution did not pass the corrected bucket calculation")

    normalized_buckets = [
        {
            "level": str(row.get("level") or ""),
            "level_no": row.get("level_no"),
            "lower_shares": row.get("lower_shares"),
            "upper_shares": row.get("upper_shares"),
            "holders": row.get("holders"),
            "shares": row.get("shares"),
            "percent": row.get("percent"),
        }
        for row in rows
    ]
    snapshot_revision_id = _digest(
        {
            "source_week_date": source_week_date,
            "stock_code": stock_code,
            "source": TDCC_SOURCE,
            "buckets": normalized_buckets,
            "summary": summary,
            "formula_version": TDCC_REVISION_VERSION,
        }
    )
    natural_key = f"{source_week_date}:{stock_code}"
    ensure_provenance_schema(conn)
    existing_rows = conn.execute(
        """
        SELECT revision_no,content_hash,payload_json
        FROM data_observation_version
        WHERE dataset_key=? AND natural_key=?
        ORDER BY revision_no
        """,
        (TDCC_DATASET_KEY, natural_key),
    ).fetchall()
    for existing in existing_rows:
        payload = _json(existing[2])
        if (
            isinstance(payload, dict)
            and payload.get("snapshot_revision_id") == snapshot_revision_id
        ):
            return {
                "written": False,
                "revision_no": int(existing[0]),
                "content_hash": str(existing[1]),
                "snapshot_revision_id": snapshot_revision_id,
            }

    expected_next = _tdcc_expected_publication(source_week_date, weeks_ahead=1)
    second_expected = _tdcc_expected_publication(source_week_date, weeks_ahead=2)
    grace_deadline = expected_next + timedelta(hours=72)
    hard_stale_at = min(expected_next + timedelta(days=14), second_expected)
    contract = DATASET_AVAILABILITY_CONTRACTS[TDCC_DATASET_KEY]
    usable_from = compute_usable_from(
        first_seen_at=observed,
        validation_passed_at=observed,
        quality_status="validated",
        maturity_stage="final",
        schema_valid=True,
        policy=contract.policy,
    )
    if usable_from is None:
        raise ValueError("TDCC revision did not pass availability policy")
    freshness_at_materialization = _tdcc_freshness(
        decision_at=observed,
        usable_from=usable_from,
        grace_deadline=grace_deadline,
        hard_stale_at=hard_stale_at,
    )
    payload = {
        "contract_version": "ChipAnalysisContractV1",
        "formula_version": TDCC_REVISION_VERSION,
        "source_policy_version": TDCC_SOURCE_POLICY_VERSION,
        "source_week_date": source_week_date,
        "stock_code": stock_code,
        "source": TDCC_SOURCE,
        "source_url": TDCC_HOLDING_DISTRIBUTION_CSV_URL,
        "publisher_published_at": None,
        "publisher_timestamp_status": "not_promised_by_official_source",
        "available_at": _tpe_text(observed),
        "expected_next_publish_at": _tpe_text(expected_next),
        "grace_deadline": _tpe_text(grace_deadline),
        "second_expected_publish_at": _tpe_text(second_expected),
        "hard_stale_at": _tpe_text(hard_stale_at),
        "snapshot_revision_id": snapshot_revision_id,
        "quality": "official_validated",
        "freshness_at_materialization": freshness_at_materialization,
        "buckets": normalized_buckets,
        "summary": summary,
        "fake_daily_observation": False,
        "identity_inference_prohibited": True,
        "candidate_contribution": 0.0,
        "referee_eligible": False,
        "next_day_outlook_eligible": False,
        "missing_weight_redistribution": False,
    }
    payload_json = _canonical_json(payload)
    content_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    revision_no = max((int(row[0]) for row in existing_rows), default=0) + 1
    now_text = _utc_text(observed)
    source_key = f"{TDCC_DATASET_KEY}:{TDCC_SOURCE}"
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
            TDCC_SOURCE,
            contract.event_semantics,
            contract.policy.required_maturity_stage,
            contract.policy.safety_delay_seconds,
            contract.policy.fallback_policy,
            TDCC_REVISION_VERSION,
            1,
            now_text,
            now_text,
        ),
    )
    event_at = datetime.combine(
        date.fromisoformat(source_week_date),
        time(23, 59, 59),
        tzinfo=TPE,
    )
    conn.execute(
        """
        INSERT INTO data_observation_version(
            dataset_key,natural_key,event_at,source_key,source_published_at,
            first_seen_at,validation_passed_at,usable_from,revised_at,revision_no,
            content_hash,schema_version,quality_status,maturity_stage,payload_json,recorded_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            TDCC_DATASET_KEY,
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
            TDCC_REVISION_VERSION,
            "validated",
            "final",
            payload_json,
            now_text,
        ),
    )
    return {
        "written": True,
        "revision_no": revision_no,
        "content_hash": content_hash,
        "snapshot_revision_id": snapshot_revision_id,
    }


def materialize_tdcc_candidate_revisions(
    conn: sqlite3.Connection,
    *,
    stock_codes: list[str] | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Freeze corrected official weekly TDCC rows as immutable revisions."""

    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tdcc_holding_distribution'"
    ).fetchone()
    if not table:
        return {
            "status": "unavailable",
            "source_snapshots": 0,
            "revisions_written": 0,
            "candidate_contribution": 0.0,
        }
    predicates = ["source=?"]
    parameters: list[Any] = [TDCC_SOURCE]
    normalized_codes = sorted({str(code).zfill(4) for code in (stock_codes or [])})
    if normalized_codes:
        placeholders = ",".join("?" for _ in normalized_codes)
        predicates.append(f"code IN ({placeholders})")
        parameters.extend(normalized_codes)
    snapshots = conn.execute(
        f"""
        SELECT DISTINCT date,code
        FROM tdcc_holding_distribution
        WHERE {' AND '.join(predicates)}
        ORDER BY date,code
        """,
        parameters,
    ).fetchall()
    observed = _aware(
        observed_at or datetime.now(TPE),
        field="observed_at",
    )
    written = 0
    replayed = 0
    rejected: list[dict[str, str]] = []
    for snapshot in snapshots:
        source_week_date, stock_code = str(snapshot[0]), str(snapshot[1]).zfill(4)
        try:
            result = _record_tdcc_candidate_revision(
                conn,
                rows=_tdcc_rows_for_date(
                    conn,
                    stock_code=stock_code,
                    source_week_date=source_week_date,
                ),
                previous_rows=_tdcc_previous_rows(
                    conn,
                    stock_code=stock_code,
                    source_week_date=source_week_date,
                ),
                observed_at=observed,
            )
        except (TypeError, ValueError) as exc:
            rejected.append(
                {
                    "source_week_date": source_week_date,
                    "stock_code": stock_code,
                    "reason": str(exc),
                }
            )
            continue
        written += int(bool(result["written"]))
        replayed += int(not result["written"])
    status = (
        "ok"
        if snapshots and not rejected
        else ("partial" if snapshots else "unavailable")
    )
    return {
        "status": status,
        "source_snapshots": len(snapshots),
        "revisions_written": written,
        "revisions_replayed": replayed,
        "rejected": rejected,
        "candidate_contribution": 0.0,
        "referee_eligible": False,
        "next_day_outlook_eligible": False,
        "source_policy_version": TDCC_SOURCE_POLICY_VERSION,
        "fake_daily_observations": 0,
    }


def read_tdcc_candidate_revision(
    conn: sqlite3.Connection,
    *,
    stock_code: str,
    target_date: str,
    decision_at: datetime,
) -> dict[str, Any]:
    """Read one weekly candidate revision with no DDL, computation, or write."""

    decision = _aware(decision_at, field="decision_at")
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='data_observation_version'"
    ).fetchone()
    if not table:
        return _tdcc_unavailable(stock_code, target_date)
    rows = conn.execute(
        """
        SELECT usable_from,revision_no,content_hash,payload_json
        FROM data_observation_version
        WHERE dataset_key=? AND natural_key LIKE ?
          AND quality_status='validated'
          AND maturity_stage IN ('final','revised')
        ORDER BY event_at DESC,revision_no DESC
        """,
        (TDCC_DATASET_KEY, f"%:{str(stock_code).zfill(4)}"),
    ).fetchall()
    eligible: list[tuple[str, datetime, int, str, dict[str, Any]]] = []
    for row in rows:
        payload = _json(row[3])
        if not isinstance(payload, dict):
            continue
        source_week_date = str(payload.get("source_week_date") or "")
        try:
            usable = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if (
            source_week_date > str(target_date)
            or usable.tzinfo is None
            or usable.astimezone(timezone.utc) > decision.astimezone(timezone.utc)
        ):
            continue
        eligible.append((source_week_date, usable, int(row[1]), str(row[2]), payload))
    if not eligible:
        return _tdcc_unavailable(stock_code, target_date)
    source_week_date, usable, revision_no, content_hash, payload = max(
        eligible,
        key=lambda item: (item[0], item[1], item[2]),
    )
    grace = datetime.fromisoformat(str(payload["grace_deadline"]).replace("Z", "+00:00"))
    hard_stale = datetime.fromisoformat(str(payload["hard_stale_at"]).replace("Z", "+00:00"))
    freshness = _tdcc_freshness(
        decision_at=decision,
        usable_from=usable,
        grace_deadline=grace,
        hard_stale_at=hard_stale,
    )
    snapshot_revision_id = str(payload.get("snapshot_revision_id") or "")
    return {
        "contract_version": "ChipAnalysisContractV1",
        "formula_version": TDCC_REVISION_VERSION,
        "stock_code": str(stock_code).zfill(4),
        "target_date": str(target_date),
        "source_week_date": source_week_date,
        "status": freshness,
        "data_eligible": freshness == "ok",
        "candidate_contribution": 0.0,
        "referee_eligible": False,
        "next_day_outlook_eligible": False,
        "revision_no": revision_no,
        "observation_content_hash": content_hash,
        "source_revision_ids": [snapshot_revision_id] if snapshot_revision_id else [],
        "source_revision_citation_count": 1 if snapshot_revision_id else 0,
        "expected_next_publish_at": payload.get("expected_next_publish_at"),
        "grace_deadline": payload.get("grace_deadline"),
        "hard_stale_at": payload.get("hard_stale_at"),
        "source_policy_version": payload.get("source_policy_version"),
        "summary": payload.get("summary"),
        "buckets": payload.get("buckets"),
        "fake_daily_observation": False,
        "request_time_computation": False,
        "request_time_writes": 0,
    }


def _tdcc_unavailable(stock_code: str, target_date: str) -> dict[str, Any]:
    return {
        "contract_version": "ChipAnalysisContractV1",
        "formula_version": TDCC_REVISION_VERSION,
        "stock_code": str(stock_code).zfill(4),
        "target_date": str(target_date),
        "status": "unavailable",
        "data_eligible": False,
        "candidate_contribution": 0.0,
        "referee_eligible": False,
        "next_day_outlook_eligible": False,
        "source_revision_ids": [],
        "source_revision_citation_count": 0,
        "fake_daily_observation": False,
        "request_time_computation": False,
        "request_time_writes": 0,
    }


def _rows_as_dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    columns = [str(item[0]) for item in cursor.description or ()]
    return [
        dict(row)
        if isinstance(row, sqlite3.Row)
        else dict(zip(columns, row, strict=True))
        for row in cursor.fetchall()
    ]


def _corporate_actions_in_cost_window(
    conn: sqlite3.Connection,
    *,
    stock_code: str,
    estimate_start_date: str,
    estimate_end_date: str,
) -> list[dict[str, Any]] | None:
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='corporate_actions'"
    ).fetchone()
    if not table:
        return None
    return _rows_as_dicts(
        conn.execute(
            """
            SELECT code,date,action_type,cash_dividend,stock_dividend,
                   source,is_confirmed,updated_at
            FROM corporate_actions
            WHERE code=? AND date BETWEEN ? AND ?
            ORDER BY date,action_type,source
            """,
            (str(stock_code).zfill(4), estimate_start_date, estimate_end_date),
        )
    )


def materialize_corporate_action_safe_cost_facts(
    conn: sqlite3.Connection,
    *,
    stock_codes: list[str] | None = None,
    trade_date: str | None = None,
    observed_at: datetime | None = None,
    corporate_action_coverage_verified: bool = False,
) -> dict[str, Any]:
    """Copy Stable estimates into zero-weight facts or suppress them safely.

    This function never recalculates the protected cost formula. Corporate
    action coverage must be explicitly proven by the caller before an
    otherwise clear estimate may remain visible as explanation-only context.
    """

    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='estimated_chip_cost_daily'"
    ).fetchone()
    if not table:
        return {
            "status": "unavailable",
            "source_estimates": 0,
            "facts_written": 0,
            "candidate_contribution": 0.0,
        }
    predicates = ["formula_version=?"]
    parameters: list[Any] = [CANONICAL_COST_FORMULA_VERSION]
    cost_placeholders = ",".join("?" for _ in CANONICAL_COST_TYPES)
    predicates.append(f"cost_type IN ({cost_placeholders})")
    parameters.extend(CANONICAL_COST_TYPES)
    normalized_codes = sorted({str(code).zfill(4) for code in (stock_codes or [])})
    if normalized_codes:
        code_placeholders = ",".join("?" for _ in normalized_codes)
        predicates.append(f"code IN ({code_placeholders})")
        parameters.extend(normalized_codes)
    if trade_date:
        predicates.append("trade_date=?")
        parameters.append(str(trade_date))
    estimates = _rows_as_dicts(
        conn.execute(
            f"""
            SELECT code,trade_date,cost_type,cost_label,estimated_cost,
                   cost_status,confidence,data_source_confidence,data_source_status,
                   source_license,source_detail,source_tables,calculation_method,
                   formula_version,price_basis,price_basis_value,
                   price_to_cost_deviation_pct,accumulation_status,position_shares,
                   total_cost_amount,cumulative_net_shares,estimate_start_date,
                   estimate_end_date,sample_days,display_reason,debug_reason,
                   missing_required_fields
            FROM estimated_chip_cost_daily
            WHERE {' AND '.join(predicates)}
            ORDER BY trade_date,code,cost_type
            """,
            parameters,
        )
    )
    observed = _aware(
        observed_at or datetime.now(TPE),
        field="observed_at",
    ).astimezone(TPE)
    observed_text = observed.isoformat(timespec="seconds")
    facts: list[dict[str, Any]] = []
    suppressed_by_reason: dict[str, int] = {}
    for estimate in estimates:
        code = str(estimate.get("code") or "").zfill(4)
        estimate_date = str(estimate.get("trade_date") or "")
        start_date = str(estimate.get("estimate_start_date") or "")
        end_date = str(estimate.get("estimate_end_date") or estimate_date)
        actions: list[dict[str, Any]] | None = None
        reason: str | None = None
        if not start_date or not end_date:
            reason = "corporate_action_window_unknown"
        else:
            actions = _corporate_actions_in_cost_window(
                conn,
                stock_code=code,
                estimate_start_date=start_date,
                estimate_end_date=end_date,
            )
            if actions is None:
                reason = "corporate_action_inventory_unavailable"
            elif any(int(action.get("is_confirmed") or 0) for action in actions):
                reason = "corporate_action_adjustment_unavailable"
            elif actions:
                reason = "corporate_action_confirmation_pending"
            elif not corporate_action_coverage_verified:
                reason = "corporate_action_coverage_unverified"
        if (
            reason is None
            and (
                estimate.get("estimated_cost") is None
                or str(estimate.get("cost_status") or "")
                not in {"ok", "estimated", "proxy_only"}
            )
        ):
            reason = "source_estimate_unavailable"

        available = reason is None
        value = estimate.get("estimated_cost") if available else None
        action_evidence = actions or []
        source_revision_id = _digest(
            {
                "estimate": estimate,
                "corporate_actions": action_evidence,
                "corporate_action_inventory_present": actions is not None,
                "corporate_action_coverage_verified": bool(
                    corporate_action_coverage_verified
                ),
                "availability_reason": reason,
                "contract_version": COST_FACT_VERSION,
            }
        )
        if reason:
            suppressed_by_reason[reason] = suppressed_by_reason.get(reason, 0) + 1
        facts.append(
            {
                "fact_id": (
                    f"v11-cost:{code}:{estimate_date}:{estimate['cost_type']}:"
                    f"{source_revision_id}"
                ),
                "entity": code,
                "field": f"{estimate['cost_type']}_cost",
                "value": value,
                "unit_currency": "TWD_per_share",
                "unit": "TWD_per_share",
                "period": f"{start_date or 'unknown'}..{end_date or 'unknown'}",
                "trade_date": estimate_date,
                "as_of": estimate_date,
                "available_at": observed_text,
                "first_seen_at": observed_text,
                "usable_from": observed_text,
                "source_revision_id": source_revision_id,
                "source_market_timestamp": None,
                "session": "post_close",
                "adjustment_basis": (
                    "corporate_action_window_verified_clear"
                    if available
                    else "corporate_action_adjustment_unavailable"
                ),
                "authority_tier": "canonical_normalized_supplemental",
                "quality": "estimated_explanation_only" if available else "unavailable",
                "availability_reason": reason,
                "use_scope": "candidate_explanation_only",
                "snapshot_id": f"candidate-cost:{source_revision_id}",
                "provenance": {
                    "source_formula_version": estimate.get("formula_version"),
                    "source_tables": estimate.get("source_tables"),
                    "calculation_method": estimate.get("calculation_method"),
                    "cost_type": estimate.get("cost_type"),
                    "is_estimated": True,
                    "true_total_holding_cost": False,
                    "corporate_action_coverage_verified": bool(
                        corporate_action_coverage_verified
                    ),
                    "corporate_actions": action_evidence,
                    "candidate_contribution": 0.0,
                    "weight": 0.0,
                    "referee_eligible": False,
                    "next_day_outlook_eligible": False,
                    "stable_formula_recomputed": False,
                    "stable_row_mutated": False,
                },
                "formula_source_version": COST_FACT_VERSION,
                "recorded_at": observed_text,
            }
        )

    existing_ids: set[str] = set()
    if facts:
        fact_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='canonical_evidence_fact'"
        ).fetchone()
        if fact_table:
            fact_ids = [str(fact["fact_id"]) for fact in facts]
            for start in range(0, len(fact_ids), 500):
                batch = fact_ids[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                existing_ids.update(
                    str(row[0])
                    for row in conn.execute(
                        f"SELECT fact_id FROM canonical_evidence_fact WHERE fact_id IN ({placeholders})",
                        batch,
                    ).fetchall()
                )
    new_facts = [fact for fact in facts if fact["fact_id"] not in existing_ids]
    written = upsert_evidence_facts(conn, new_facts) if new_facts else 0
    available_count = len(facts) - sum(suppressed_by_reason.values())
    status = (
        "ok"
        if facts and available_count == len(facts)
        else ("partial" if available_count else "unavailable")
    )
    return {
        "status": status,
        "source_estimates": len(estimates),
        "facts_built": len(facts),
        "facts_written": written,
        "available_explanation_only": available_count,
        "suppressed": sum(suppressed_by_reason.values()),
        "suppressed_by_reason": dict(sorted(suppressed_by_reason.items())),
        "corporate_action_coverage_verified": bool(corporate_action_coverage_verified),
        "candidate_contribution": 0.0,
        "referee_eligible": False,
        "next_day_outlook_eligible": False,
        "stable_formula_recomputed": False,
        "stable_rows_mutated": 0,
    }


def read_corporate_action_safe_cost_facts(
    conn: sqlite3.Connection,
    *,
    stock_code: str,
    target_date: str,
    decision_at: datetime,
) -> dict[str, Any]:
    """Read already-materialized cost facts without DDL or writes."""

    decision = _aware(decision_at, field="decision_at").astimezone(timezone.utc)
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='canonical_evidence_fact'"
    ).fetchone()
    if not table:
        return _cost_fact_result(stock_code, target_date, [])
    rows = _rows_as_dicts(
        conn.execute(
            """
            SELECT fact_id,field,value_json,unit,trade_date,available_at,
                   quality,availability_reason,source_revision_id,provenance_json
            FROM canonical_evidence_fact
            WHERE entity=? AND trade_date<=? AND formula_source_version=?
            ORDER BY trade_date DESC,available_at DESC,fact_id
            """,
            (str(stock_code).zfill(4), str(target_date), COST_FACT_VERSION),
        )
    )
    visible: list[dict[str, Any]] = []
    for row in rows:
        try:
            available_at = datetime.fromisoformat(
                str(row.get("available_at") or "").replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if (
            available_at.tzinfo is None
            or available_at.astimezone(timezone.utc) > decision
        ):
            continue
        visible.append(
            {
                "fact_id": row.get("fact_id"),
                "field": row.get("field"),
                "value": _json(row.get("value_json")),
                "unit": row.get("unit"),
                "trade_date": row.get("trade_date"),
                "available_at": row.get("available_at"),
                "quality": row.get("quality"),
                "availability_reason": row.get("availability_reason"),
                "source_revision_id": row.get("source_revision_id"),
                "provenance": _json(row.get("provenance_json")) or {},
            }
        )
    if not visible:
        return _cost_fact_result(stock_code, target_date, [])
    newest_date = max(str(item["trade_date"]) for item in visible)
    latest_by_field: dict[str, dict[str, Any]] = {}
    for item in visible:
        if str(item["trade_date"]) == newest_date:
            latest_by_field.setdefault(str(item["field"]), item)
    return _cost_fact_result(
        stock_code,
        target_date,
        list(latest_by_field.values()),
    )


def _cost_fact_result(
    stock_code: str,
    target_date: str,
    facts: list[dict[str, Any]],
) -> dict[str, Any]:
    available = sum(1 for fact in facts if fact.get("quality") != "unavailable")
    status = (
        "ok"
        if facts and available == len(facts)
        else ("partial" if available else "unavailable")
    )
    return {
        "contract_version": COST_FACT_VERSION,
        "stock_code": str(stock_code).zfill(4),
        "target_date": str(target_date),
        "status": status,
        "facts": sorted(facts, key=lambda item: str(item["field"])),
        "candidate_contribution": 0.0,
        "weight": 0.0,
        "referee_eligible": False,
        "next_day_outlook_eligible": False,
        "request_time_computation": False,
        "request_time_writes": 0,
    }
