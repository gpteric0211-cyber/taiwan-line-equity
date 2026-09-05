from __future__ import annotations

"""Prospective official T+1 outcome materialization for sealed predictions."""

import json
import math
import re
import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from analysis.target_label_contract_v1 import (
    OFFICIAL_ADJUSTMENT_BASIS,
    build_outcome_rows,
    derive_target_labels,
)
from repository.single_track_v3_repository import record_target_outcomes


TPE = ZoneInfo("Asia/Taipei")
OUTCOME_REVISION = "official-adjusted-v1"


def _payload(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _epoch_to_iso(value: Any) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return datetime.fromtimestamp(number, tz=TPE).isoformat(timespec="seconds")


def _prediction_sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT p.sample_id,p.analysis_id,p.analysis_cutoff,
                       a.canonical_payload_json
                FROM analysis_target_prediction p
                JOIN canonical_analysis_artifact a ON a.analysis_id=p.analysis_id
                WHERE NOT EXISTS(
                    SELECT 1 FROM analysis_target_outcome o
                    WHERE o.sample_id=p.sample_id
                      AND o.target_key=p.target_key
                      AND o.outcome_revision=?
                )
                GROUP BY p.sample_id,p.analysis_id,p.analysis_cutoff,a.canonical_payload_json
                ORDER BY p.analysis_cutoff,p.sample_id
                """,
                (OUTCOME_REVISION,),
            ).fetchall()
        ]
    finally:
        conn.row_factory = old_factory


def _market_session_after(conn: sqlite3.Connection, prediction_date: str) -> str | None:
    row = conn.execute(
        """
        SELECT date FROM history_price
        WHERE date>? AND close IS NOT NULL AND close>0
        GROUP BY date HAVING COUNT(*)>=500
        ORDER BY date LIMIT 1
        """,
        (prediction_date,),
    ).fetchone()
    return str(row[0]) if row else None


def _official_price(
    conn: sqlite3.Connection,
    *,
    code: str,
    trade_date: str,
) -> dict[str, Any] | None:
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT date,code,open,close,source,source_quality,fetched_at
            FROM history_price WHERE code=? AND date=? LIMIT 1
            """,
            (code, trade_date),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.row_factory = old_factory


def _has_corporate_action(
    conn: sqlite3.Connection,
    *,
    code: str,
    prediction_date: str,
    outcome_date: str,
) -> bool:
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='corporate_actions'"
    ).fetchone()
    if not table:
        return True
    return bool(
        conn.execute(
            """
            SELECT 1 FROM corporate_actions
            WHERE code=? AND date>? AND date<=? AND is_confirmed=1 LIMIT 1
            """,
            (code, prediction_date, outcome_date),
        ).fetchone()
    )


def _unavailable_labels(reason: str) -> dict[str, Any]:
    return {
        "quality_status": "unavailable",
        "targets": {
            target: {
                "label": None,
                "quality_status": "unavailable",
                "availability_reason": reason,
            }
            for target in (
                "next_open_gap",
                "continuation_reversal",
                "next_close_direction",
            )
        },
        "prices": {"adjustment_basis": OFFICIAL_ADJUSTMENT_BASIS},
    }


def materialize_available_target_outcomes(
    conn: sqlite3.Connection,
    *,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Write only outcomes that became knowable after a real sealed prediction."""

    now = recorded_at or datetime.now(TPE).isoformat(timespec="seconds")
    results: list[dict[str, Any]] = []
    rows_to_write: list[dict[str, Any]] = []
    for source in _prediction_sources(conn):
        payload = _payload(source.get("canonical_payload_json"))
        code = str(payload.get("code") or "").strip()
        prediction_date = str(payload.get("trade_date") or "").strip()
        sample_id = str(source.get("sample_id") or "")
        if not re.fullmatch(r"\d{4}", code) or not prediction_date:
            results.append(
                {
                    "sample_id": sample_id,
                    "status": "invalid_prediction_metadata",
                }
            )
            continue
        outcome_date = _market_session_after(conn, prediction_date)
        if not outcome_date:
            results.append({"sample_id": sample_id, "status": "awaiting_next_session"})
            continue
        t_row = _official_price(conn, code=code, trade_date=prediction_date)
        next_row = _official_price(conn, code=code, trade_date=outcome_date)
        if next_row is None:
            results.append(
                {
                    "sample_id": sample_id,
                    "status": "awaiting_or_no_trade",
                    "outcome_trade_date": outcome_date,
                }
            )
            continue
        available_at = _epoch_to_iso(next_row.get("fetched_at"))
        if available_at is None:
            results.append(
                {
                    "sample_id": sample_id,
                    "status": "outcome_availability_unknown",
                    "outcome_trade_date": outcome_date,
                }
            )
            continue
        try:
            if datetime.fromisoformat(available_at) <= datetime.fromisoformat(
                str(source.get("analysis_cutoff") or "")
            ):
                results.append(
                    {
                        "sample_id": sample_id,
                        "status": "outcome_not_strictly_after_cutoff",
                    }
                )
                continue
        except ValueError:
            results.append({"sample_id": sample_id, "status": "invalid_analysis_cutoff"})
            continue
        official_quality = all(
            row is not None
            and str(row.get("source_quality") or "").lower() in {"official", "canonical_official"}
            for row in (t_row, next_row)
        )
        if not official_quality:
            labels = _unavailable_labels("official_price_quality_failed")
        elif _has_corporate_action(
            conn,
            code=code,
            prediction_date=prediction_date,
            outcome_date=outcome_date,
        ):
            labels = _unavailable_labels("corporate_action_adjustment_unavailable")
        else:
            assert t_row is not None
            labels = derive_target_labels(
                t_close=t_row.get("close"),
                next_open=next_row.get("open"),
                next_close=next_row.get("close"),
                adjustment_basis=OFFICIAL_ADJUSTMENT_BASIS,
            )
        rows = build_outcome_rows(
            sample_id=sample_id,
            stock_code=code,
            prediction_trade_date=prediction_date,
            outcome_trade_date=outcome_date,
            outcome_revision=OUTCOME_REVISION,
            available_at=available_at,
            recorded_at=now,
            labels=labels,
        )
        rows_to_write.extend(rows)
        results.append(
            {
                "sample_id": sample_id,
                "status": "ready",
                "outcome_trade_date": outcome_date,
                "quality_status": labels.get("quality_status"),
            }
        )
    written = record_target_outcomes(conn, rows_to_write) if rows_to_write else 0
    return {
        "ok": True,
        "prediction_samples_seen": len(results),
        "outcome_rows_ready": len(rows_to_write),
        "outcome_rows_written": written,
        "results": results,
    }
