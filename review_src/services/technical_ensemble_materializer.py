from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from analysis.technical_ensemble_v1 import (
    COMPONENT_SPECS,
    TECHNICAL_ENSEMBLE_VERSION,
    TECHNICAL_FORMULA_VERSION_V1,
    TECHNICAL_MINIMUM_HISTORY_ROWS,
    compute_technical_ensemble_frame,
    finite_number,
)
from core.data_quality import assess_daily_ohlcv
from core.db import db
from core.market_analytics_schema import ensure_market_analytics_schema
from core.market_foundation_schema import source_rank
from core.single_track_v3_schema import (
    TECHNICAL_COMPONENT_RETENTION_TRADING_DAYS,
    ensure_single_track_v3_schema,
)
from core.technical_component_quality import assess_technical_component_quality
from repository.history_repository import history_date_coverage, recent_market_reference_dates
from repository.market_analytics_repository import all_history_rows_for_technical_ensemble
from repository.rsi_adjustment_repository import (
    apply_rsi_split_adjustments,
    load_rsi_split_adjustments,
)
from repository.single_track_v3_repository import (
    prune_technical_components,
    prune_technical_vectors,
    upsert_technical_components,
    upsert_technical_states,
    upsert_technical_vectors,
)


TPE = ZoneInfo("Asia/Taipei")
INPUT_DIGEST_POLICY = "sha256-chain-v1"
ADJUSTMENT_BASIS_VERSION = "verified-split-adjusted-ohlc-raw-volume-v1"
TECHNICAL_WRITE_COMMIT_INTERVAL_CODES = 50


def _default_codes(conn) -> list[str]:
    active = [
        str(row[0])
        for row in conn.execute(
            """
            SELECT code FROM stock_master
            WHERE is_active=1 AND security_type='stock'
            ORDER BY market,code
            """
        ).fetchall()
    ]
    if active:
        return active
    return [
        str(row[0])
        for row in conn.execute("SELECT code FROM stock_industry_profile ORDER BY code").fetchall()
    ]


def _digestable_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": str(row.get("date") or ""),
        "open": row.get("technical_open") if row.get("technical_open") is not None else row.get("open"),
        "high": row.get("technical_high") if row.get("technical_high") is not None else row.get("high"),
        "low": row.get("technical_low") if row.get("technical_low") is not None else row.get("low"),
        "close": row.get("technical_close") if row.get("technical_close") is not None else row.get("close"),
        "volume_shares": row.get("volume"),
        "source": str(row.get("source") or ""),
        "source_quality": str(row.get("source_quality") or ""),
    }


def input_snapshot_digest_chain(rows: Iterable[dict[str, Any]]) -> list[str]:
    digest = b""
    values: list[str] = []
    for row in rows:
        encoded = json.dumps(
            _digestable_row(row),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        digest = hashlib.sha256(digest + b"\n" + encoded).digest()
        values.append(digest.hex())
    return values


def _adjustment_basis(events: list[dict[str, Any]]) -> str:
    payload = [
        {
            "event_date": str(event.get("event_date") or ""),
            "pre_event_factor": event.get("pre_event_factor"),
            "source": str(event.get("source") or ""),
        }
        for event in events
    ]
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"{ADJUSTMENT_BASIS_VERSION}:{digest}"


def _component_rows(
    code: str,
    history: list[dict[str, Any]],
    frame,
    digests: list[str],
    indexes: Iterable[int],
    *,
    adjustment_basis: str,
    computed_at: str,
    latest_coverage: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    official_prefix_count = 0
    official_counts: list[int] = []
    for source in history:
        if source_rank(source.get("source")) >= 100:
            official_prefix_count += 1
        official_counts.append(official_prefix_count)
    for index in indexes:
        source_row = history[index]
        input_count = index + 1
        official_count = official_counts[index]
        source_quality = (
            "official"
            if official_count == input_count
            else "mixed"
            if official_count
            else "fallback"
        )
        latest_official = source_rank(source_row.get("source")) >= 100
        for spec in COMPONENT_SPECS:
            raw_value = frame.iloc[index].get(spec["column"])
            value_kind = str(spec.get("value_kind") or "number")
            value = None if value_kind == "text" else finite_number(raw_value)
            value_text = (
                str(raw_value)
                if value_kind == "text" and raw_value is not None and str(raw_value) not in {"", "None", "nan"}
                else None
            )
            warmup = int(spec["warmup"])
            quality = assess_technical_component_quality(
                input_row_count=input_count,
                warmup_rows=warmup,
                value_available=value is not None or value_text is not None,
                latest_source_official=latest_official,
                recent_coverage=latest_coverage,
                full_ensemble_component=spec["component_key"] == "ensemble_score",
                full_ensemble_minimum_rows=TECHNICAL_MINIMUM_HISTORY_ROWS,
            )
            data_quality = str(quality["status"])
            quality_reason = str(quality["quality_reason"])
            decision_ready = bool(quality["decision_ready"])
            parameters = {
                **dict(spec.get("parameters") or {}),
                "ensemble_version": TECHNICAL_ENSEMBLE_VERSION,
                "input_digest_policy": INPUT_DIGEST_POLICY,
                "nan_inf_rule": "unavailable_not_zero",
                "volume_unit": "shares",
            }
            rows.append(
                {
                    "trade_date": str(source_row.get("date") or ""),
                    "stock_code": code,
                    "indicator_key": spec["indicator_key"],
                    "component_key": spec["component_key"],
                    "value": value,
                    "value_text": value_text,
                    "unit": spec["unit"],
                    "parameters": parameters,
                    "formula_version": TECHNICAL_FORMULA_VERSION_V1,
                    "input_snapshot_digest": digests[index],
                    "input_start_date": str(history[0].get("date") or ""),
                    "input_end_date": str(source_row.get("date") or ""),
                    "input_row_count": input_count,
                    "adjustment_basis": adjustment_basis,
                    "source_quality": source_quality,
                    "data_quality": data_quality,
                    "availability_reason": quality["availability_reason"],
                    "decision_ready": 1 if decision_ready else 0,
                    "quality_reason": quality_reason,
                    "computed_at": computed_at,
                }
            )
    return rows


def _state_rows(
    code: str,
    history: list[dict[str, Any]],
    frame,
    digests: list[str],
    *,
    computed_at: str,
) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    latest = frame.iloc[-1]
    rows = []
    for indicator_key in ("obv", "ad", "nvi", "pvi"):
        value = finite_number(latest.get(indicator_key))
        if value is None:
            continue
        rows.append(
            {
                "stock_code": code,
                "indicator_key": indicator_key,
                "state_key": "continuous_level",
                "formula_version": TECHNICAL_FORMULA_VERSION_V1,
                "state_value": value,
                "state": {
                    "seed": 0 if indicator_key in {"obv", "ad"} else 1000,
                    "input_start_date": str(history[0].get("date") or ""),
                    "input_row_count": len(history),
                    "state_policy": "complete_available_history_prefix-v1",
                },
                "last_trade_date": str(history[-1].get("date") or ""),
                "input_snapshot_digest": digests[-1],
                "updated_at": computed_at,
            }
        )
    return rows


def _vector_rows(component_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact normalized components without losing values or quality exceptions."""

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in component_rows:
        grouped.setdefault((str(row["trade_date"]), str(row["stock_code"])), []).append(row)
    vectors: list[dict[str, Any]] = []
    for (_trade_date, _stock_code), rows in grouped.items():
        first = rows[0]
        values: dict[str, Any] = {}
        unavailable: dict[str, Any] = {}
        ready_count = 0
        for row in rows:
            key = f"{row['indicator_key']}.{row['component_key']}"
            values[key] = row["value"] if row.get("value") is not None else row.get("value_text")
            if int(row.get("decision_ready") or 0):
                ready_count += 1
            else:
                unavailable[key] = {
                    "data_quality": row.get("data_quality"),
                    "availability_reason": row.get("availability_reason"),
                    "quality_reason": row.get("quality_reason"),
                }
        vectors.append(
            {
                "trade_date": first["trade_date"],
                "stock_code": first["stock_code"],
                "formula_version": first["formula_version"],
                "ensemble_version": TECHNICAL_ENSEMBLE_VERSION,
                "values": values,
                "unavailable": unavailable,
                "component_count": len(rows),
                "decision_ready_count": ready_count,
                "input_snapshot_digest": first["input_snapshot_digest"],
                "input_start_date": first["input_start_date"],
                "input_end_date": first["input_end_date"],
                "input_row_count": first["input_row_count"],
                "adjustment_basis": first["adjustment_basis"],
                "source_quality": first["source_quality"],
                "data_quality": "ready" if ready_count == len(rows) else "partial",
                "quality_reason": (
                    "all_components_ready"
                    if ready_count == len(rows)
                    else f"{len(rows) - ready_count}_components_unavailable"
                ),
                "computed_at": first["computed_at"],
            }
        )
    return vectors


def _backfill_vector_rows(
    code: str,
    history: list[dict[str, Any]],
    frame,
    digests: list[str],
    indexes: Iterable[int],
    *,
    adjustment_basis: str,
    computed_at: str,
) -> list[dict[str, Any]]:
    """Build historical vectors directly from the frame, avoiding 62 ORM rows/day."""

    selected_indexes = list(indexes)
    if not selected_indexes:
        return []
    key_by_column = {
        str(spec["column"]): f"{spec['indicator_key']}.{spec['component_key']}"
        for spec in COMPONENT_SPECS
    }
    selected_columns = list(key_by_column)
    records = json.loads(
        frame.iloc[selected_indexes][selected_columns]
        .rename(columns=key_by_column)
        .to_json(orient="records", double_precision=15)
    )
    official_prefix = 0
    official_counts: list[int] = []
    for source in history:
        if source_rank(source.get("source")) >= 100:
            official_prefix += 1
        official_counts.append(official_prefix)
    spec_by_key = {
        f"{spec['indicator_key']}.{spec['component_key']}": spec
        for spec in COMPONENT_SPECS
    }
    vectors: list[dict[str, Any]] = []
    for index, values in zip(selected_indexes, records, strict=True):
        source_row = history[index]
        input_count = index + 1
        official_count = official_counts[index]
        source_quality = (
            "official"
            if official_count == input_count
            else "mixed"
            if official_count
            else "fallback"
        )
        latest_official = source_rank(source_row.get("source")) >= 100
        unavailable_counts: dict[str, int] = {}
        ready_count = 0
        for key, value in values.items():
            spec = spec_by_key[key]
            if input_count < int(spec["warmup"]):
                reason = "insufficient_history"
            elif value is None:
                reason = "unavailable"
            elif not latest_official:
                reason = "fallback_source"
            elif (
                spec["component_key"] == "ensemble_score"
                and input_count < TECHNICAL_MINIMUM_HISTORY_ROWS
            ):
                reason = "insufficient_history"
            else:
                ready_count += 1
                continue
            unavailable_counts[reason] = unavailable_counts.get(reason, 0) + 1
        component_count = len(COMPONENT_SPECS)
        vectors.append(
            {
                "trade_date": str(source_row.get("date") or ""),
                "stock_code": code,
                "formula_version": TECHNICAL_FORMULA_VERSION_V1,
                "ensemble_version": TECHNICAL_ENSEMBLE_VERSION,
                "values": values,
                "unavailable": {
                    "counts_by_reason": unavailable_counts,
                    "detail_policy": "reconstruct_from_formula_spec_and_null_values",
                },
                "component_count": component_count,
                "decision_ready_count": ready_count,
                "input_snapshot_digest": digests[index],
                "input_start_date": str(history[0].get("date") or ""),
                "input_end_date": str(source_row.get("date") or ""),
                "input_row_count": input_count,
                "adjustment_basis": adjustment_basis,
                "source_quality": source_quality,
                "data_quality": "ready" if ready_count == component_count else "partial",
                "quality_reason": (
                    "all_components_ready"
                    if ready_count == component_count
                    else f"{component_count - ready_count}_components_unavailable"
                ),
                "computed_at": computed_at,
            }
        )
    return vectors


def materialize_technical_ensemble_v1(
    *,
    codes: list[str] | None = None,
    trade_date: str | None = None,
    backfill: bool = False,
    retain_trading_days: int = TECHNICAL_COMPONENT_RETENTION_TRADING_DAYS,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Scheduled candidate materializer; this function is never called by a request route."""

    normalized_codes = sorted(
        {
            str(code or "").strip().zfill(4)
            for code in (codes or [])
            if str(code or "").strip().isdigit()
        }
    )
    computed_at = datetime.now(TPE).isoformat(timespec="seconds")
    component_rows = 0
    vector_rows = 0
    state_rows = 0
    decision_ready_rows = 0
    unavailable_rows = 0
    missing_codes: list[str] = []
    code_results: list[dict[str, Any]] = []
    write_batch_count = 0
    pending_write_codes = 0
    retention = max(int(retain_trading_days), TECHNICAL_COMPONENT_RETENTION_TRADING_DAYS)

    with closing(db()) as conn:
        if not dry_run:
            ensure_market_analytics_schema(conn)
            ensure_single_track_v3_schema(conn)
        selected_codes = normalized_codes or _default_codes(conn)
        for code in selected_codes:
            history = all_history_rows_for_technical_ensemble(
                conn,
                code,
                end_date=trade_date,
            )
            history = [row for row in history if assess_daily_ohlcv(row).get("ready")]
            if trade_date:
                history = [row for row in history if str(row.get("date") or "") <= trade_date]
            if not history or (trade_date and str(history[-1].get("date") or "") != trade_date):
                missing_codes.append(code)
                continue
            events = load_rsi_split_adjustments(conn, code)
            adjusted = apply_rsi_split_adjustments(history, events)
            frame = compute_technical_ensemble_frame(adjusted)
            digests = input_snapshot_digest_chain(adjusted)
            latest_coverage = None
            if not backfill:
                latest_date = str(adjusted[-1].get("date") or "")
                reference_dates = recent_market_reference_dates(
                    conn,
                    TECHNICAL_MINIMUM_HISTORY_ROWS,
                    latest_completed_date=latest_date,
                )
                latest_coverage = history_date_coverage(
                    conn,
                    code,
                    required_days=TECHNICAL_MINIMUM_HISTORY_ROWS,
                    reference_dates=reference_dates,
                )
            first_index = max(0, len(adjusted) - retention)
            indexes = range(first_index, len(adjusted)) if backfill else [len(adjusted) - 1]
            adjustment_basis = _adjustment_basis(events)
            if backfill:
                candidate_vectors = _backfill_vector_rows(
                    code,
                    adjusted,
                    frame,
                    digests,
                    indexes,
                    adjustment_basis=adjustment_basis,
                    computed_at=computed_at,
                )
                candidate_rows = _component_rows(
                    code,
                    adjusted,
                    frame,
                    digests,
                    [len(adjusted) - 1],
                    adjustment_basis=adjustment_basis,
                    computed_at=computed_at,
                    latest_coverage=latest_coverage,
                )
                conceptual_component_count = len(candidate_vectors) * len(COMPONENT_SPECS)
                ready = sum(int(row["decision_ready_count"]) for row in candidate_vectors)
            else:
                candidate_rows = _component_rows(
                    code,
                    adjusted,
                    frame,
                    digests,
                    indexes,
                    adjustment_basis=adjustment_basis,
                    computed_at=computed_at,
                    latest_coverage=latest_coverage,
                )
                candidate_vectors = _vector_rows(candidate_rows)
                conceptual_component_count = len(candidate_rows)
                ready = sum(int(row["decision_ready"]) for row in candidate_rows)
            decision_ready_rows += ready
            unavailable_rows += conceptual_component_count - ready
            candidate_states = _state_rows(
                code,
                adjusted,
                frame,
                digests,
                computed_at=computed_at,
            )
            if not dry_run:
                component_rows += upsert_technical_components(conn, candidate_rows)
                vector_rows += upsert_technical_vectors(conn, candidate_vectors)
                state_rows += upsert_technical_states(conn, candidate_states)
                pending_write_codes += 1
                if pending_write_codes >= TECHNICAL_WRITE_COMMIT_INTERVAL_CODES:
                    conn.commit()
                    write_batch_count += 1
                    pending_write_codes = 0
            code_results.append(
                {
                    "code": code,
                    "history_rows": len(adjusted),
                    "snapshot_rows": len(list(indexes)),
                    "component_rows": conceptual_component_count,
                    "vector_rows": len(candidate_vectors),
                    "decision_ready_components": ready,
                    "latest_date": str(adjusted[-1].get("date") or ""),
                    "input_snapshot_digest": digests[-1],
                    "adjustment_basis": _adjustment_basis(events),
                }
            )
        if not dry_run and pending_write_codes:
            conn.commit()
            write_batch_count += 1
            pending_write_codes = 0
        prune = (
            {"skipped": True, "reason": "dry_run"}
            if dry_run
            else prune_technical_components(conn, retain_trading_days=retention)
        )
        vector_prune = (
            {"skipped": True, "reason": "dry_run"}
            if dry_run
            else prune_technical_vectors(conn, retain_trading_days=retention)
        )
        if not dry_run:
            conn.commit()

    return {
        "ok": bool(code_results),
        "status": "ok" if code_results and not missing_codes else "partial",
        "dry_run": dry_run,
        "backfill": backfill,
        "trade_date": trade_date,
        "ensemble_version": TECHNICAL_ENSEMBLE_VERSION,
        "formula_version": TECHNICAL_FORMULA_VERSION_V1,
        "input_digest_policy": INPUT_DIGEST_POLICY,
        "retention_trading_days": retention,
        "selected_code_count": len(selected_codes),
        "processed_code_count": len(code_results),
        "missing_history_code_count": len(missing_codes),
        "missing_history_codes": missing_codes[:100],
        "component_rows_written": component_rows,
        "vector_rows_written": vector_rows,
        "historical_storage": "one_versioned_feature_vector_per_stock_trade_date",
        "normalized_component_scope": "latest_trade_date_only_when_backfill",
        "state_rows_written": state_rows,
        "decision_ready_component_count": decision_ready_rows,
        "not_ready_component_count": unavailable_rows,
        "request_path_computation_count": 0,
        "request_path_write_count": 0,
        "write_commit_interval_codes": TECHNICAL_WRITE_COMMIT_INTERVAL_CODES,
        "write_batch_count": write_batch_count,
        "prune": prune,
        "vector_prune": vector_prune,
        "code_result_sample": code_results[:50],
        "code_results_truncated": len(code_results) > 50,
    }
