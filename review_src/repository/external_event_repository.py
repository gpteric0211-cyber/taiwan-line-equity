from __future__ import annotations

import json
import sqlite3
from core.material_news_schema import archive_material_news
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from core.external_event_schema import ensure_external_event_schema
from core.market_timing import (
    analysis_cutoff_for_reference,
    availability_contract,
    next_taiwan_trading_date,
)


def upsert_external_market_events(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    ensure_external_event_schema(conn)
    data = [dict(row) for row in rows]
    if not data:
        return 0
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    for row in data:
        row["_timing"] = availability_contract(
            fetched_at=fetched_at,
            published_at=row.get("published_at"),
        )
    conn.executemany(
        """
        INSERT INTO external_market_event(
            event_key,event_date,published_at,code,source_id,publisher,source_url,
            source_class,event_type,title,summary_excerpt,direction,confidence,
            time_horizon,affected_terms_json,metrics_json,quality_status,
            source_quality,license_class,analysis_version,
            content_fingerprint,reliability_score,reference_value_score,
            can_override_main_status,fetched_at,available_at,market_session,
            effective_tw_trade_date
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(event_key) DO UPDATE SET
            published_at=excluded.published_at,
            title=excluded.title,
            summary_excerpt=excluded.summary_excerpt,
            direction=excluded.direction,
            confidence=excluded.confidence,
            time_horizon=excluded.time_horizon,
            affected_terms_json=excluded.affected_terms_json,
            metrics_json=excluded.metrics_json,
            quality_status=excluded.quality_status,
            analysis_version=excluded.analysis_version,
            content_fingerprint=excluded.content_fingerprint,
            reliability_score=excluded.reliability_score,
            reference_value_score=excluded.reference_value_score,
            fetched_at=excluded.fetched_at,
            available_at=excluded.available_at,
            market_session=excluded.market_session,
            effective_tw_trade_date=excluded.effective_tw_trade_date
        """,
        [
            (
                row.get("event_key"), row.get("event_date"), row.get("published_at"), row.get("code"),
                row.get("source_id"), row.get("publisher"), row.get("source_url"),
                row.get("source_class"), row.get("event_type"), row.get("title"),
                row.get("summary_excerpt"), row.get("direction") or "unknown",
                row.get("confidence") or "low", row.get("time_horizon") or "unknown",
                json.dumps(row.get("affected_terms") or [], ensure_ascii=False, separators=(",", ":")),
                json.dumps(row.get("metrics") or {}, ensure_ascii=False, separators=(",", ":")),
                row.get("quality_status") or "ok", row.get("source_quality"),
                row.get("license_class"), row.get("analysis_version"),
                row.get("content_fingerprint") or row.get("event_key"),
                float(row.get("reliability_score") or 0),
                float(row.get("reference_value_score") or 0),
                0, fetched_at, row["_timing"]["available_at"],
                row["_timing"]["market_session"],
                row["_timing"]["effective_tw_trade_date"],
            )
            for row in data
        ],
    )
    archive_material_news(conn, source_tables=("external_market_event",))
    return len(data)


def prune_external_market_events(conn: sqlite3.Connection, retain_days: int = 730) -> int:
    archive_material_news(conn, source_tables=("external_market_event",))
    cursor = conn.execute(
        "DELETE FROM external_market_event WHERE event_date < date('now', ?)",
        (f"-{max(int(retain_days), 30)} days",),
    )
    return max(0, int(cursor.rowcount or 0))


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()] if isinstance(parsed, list) else []


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def read_stock_external_market_events(
    conn: sqlite3.Connection,
    *,
    code: str,
    reference_date: str,
    stock_terms: Iterable[str],
    lookback_days: int = 45,
    limit: int = 12,
    analysis_cutoff: str | None = None,
) -> list[dict[str, Any]]:
    """Return direct filings plus policy events mapped by audited taxonomy terms."""

    terms = {str(item).strip().lower() for item in stock_terms if str(item).strip()}
    effective_date = next_taiwan_trading_date(reference_date, strictly_after=True)
    cutoff = analysis_cutoff_for_reference(
        effective_date,
        explicit_cutoff=analysis_cutoff,
    )
    rows = conn.execute(
        """
        SELECT event_key,event_date,published_at,code,source_id,publisher,source_url,
               source_class,event_type,title,summary_excerpt,direction,confidence,
               time_horizon,affected_terms_json,metrics_json,quality_status,
               source_quality,license_class,analysis_version,
               content_fingerprint,reliability_score,reference_value_score,
               can_override_main_status,fetched_at,available_at,market_session,
               effective_tw_trade_date
        FROM external_market_event
        WHERE event_date<=?
          AND event_date>=date(?, ?)
          AND quality_status='ok'
          AND source_quality IN ('official','licensed')
          AND (code=? OR code IS NULL)
          AND effective_tw_trade_date<=?
          AND available_at IS NOT NULL
          AND datetime(available_at)<=datetime(?)
        ORDER BY event_date DESC, COALESCE(published_at,'') DESC
        LIMIT 250
        """,
        (
            effective_date,
            reference_date,
            f"-{max(int(lookback_days), 1)} days",
            code,
            effective_date,
            cutoff,
        ),
    ).fetchall()
    output: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        affected_terms = _json_list(row.pop("affected_terms_json", "[]"))
        direct = str(row.get("code") or "") == code
        matched_terms = sorted({term for term in affected_terms if term.lower() in terms})
        if not direct and not matched_terms:
            continue
        row["affected_terms"] = affected_terms
        row["matched_stock_terms"] = matched_terms
        row["mapping_method"] = "direct_stock_code" if direct else "official_industry_taxonomy"
        row["metrics"] = _json_object(row.pop("metrics_json", "{}"))
        row["can_override_main_status"] = False
        output.append(row)
        if len(output) >= max(1, min(int(limit), 30)):
            break
    return output


def read_general_external_market_events(
    conn: sqlite3.Connection,
    *,
    reference_date: str,
    lookback_days: int = 21,
    limit: int = 80,
    analysis_cutoff: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent trusted market-wide events without inventing stock mappings."""

    effective_date = next_taiwan_trading_date(reference_date, strictly_after=True)
    cutoff = analysis_cutoff_for_reference(
        effective_date,
        explicit_cutoff=analysis_cutoff,
    )
    rows = conn.execute(
        """
        SELECT event_key,event_date,published_at,code,source_id,publisher,source_url,
               source_class,event_type,title,summary_excerpt,direction,confidence,
               time_horizon,affected_terms_json,metrics_json,quality_status,
               source_quality,license_class,analysis_version,
               content_fingerprint,reliability_score,reference_value_score,
               can_override_main_status,fetched_at,available_at,market_session,
               effective_tw_trade_date
        FROM external_market_event
        WHERE event_date<=?
          AND event_date>=date(?, ?)
          AND quality_status='ok'
          AND source_quality IN ('official','licensed')
          AND code IS NULL
          AND effective_tw_trade_date<=?
          AND available_at IS NOT NULL
          AND datetime(available_at)<=datetime(?)
        ORDER BY event_date DESC, COALESCE(published_at,'') DESC,
                 reference_value_score DESC, reliability_score DESC
        LIMIT ?
        """,
        (
            effective_date,
            reference_date,
            f"-{max(int(lookback_days), 1)} days",
            effective_date,
            cutoff,
            max(1, min(int(limit), 250)),
        ),
    ).fetchall()
    output: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        row["affected_terms"] = _json_list(row.pop("affected_terms_json", "[]"))
        row["matched_stock_terms"] = []
        row["mapping_method"] = "general_market_event"
        row["metrics"] = _json_object(row.pop("metrics_json", "{}"))
        row["can_override_main_status"] = False
        output.append(row)
    return output
