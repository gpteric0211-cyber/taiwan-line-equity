from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from core.material_news_schema import archive_material_news
from core.news_radar_schema import ensure_news_radar_schema
from core.market_timing import (
    analysis_cutoff_for_reference,
    availability_contract,
    next_taiwan_trading_date,
)


def upsert_news_radar_events(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    ensure_news_radar_schema(conn)
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
        INSERT INTO news_radar_event(
            event_key,event_date,published_at,source_id,publisher,source_url,title,
            affected_terms_json,metrics_json,quality_status,source_quality,
            verification_status,license_class,analysis_version,content_fingerprint,
            reliability_score,reference_value_score,can_override_main_status,fetched_at,
            available_at,market_session,effective_tw_trade_date
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(event_key) DO UPDATE SET
            published_at=excluded.published_at,
            publisher=excluded.publisher,
            title=excluded.title,
            affected_terms_json=excluded.affected_terms_json,
            metrics_json=excluded.metrics_json,
            quality_status=excluded.quality_status,
            reference_value_score=excluded.reference_value_score,
            fetched_at=excluded.fetched_at,
            available_at=excluded.available_at,
            market_session=excluded.market_session,
            effective_tw_trade_date=excluded.effective_tw_trade_date
        """,
        [
            (
                row.get("event_key"), row.get("event_date"), row.get("published_at"),
                row.get("source_id"), row.get("publisher"), row.get("source_url"),
                row.get("title"),
                json.dumps(row.get("affected_terms") or [], ensure_ascii=False, separators=(",", ":")),
                json.dumps(row.get("metrics") or {}, ensure_ascii=False, separators=(",", ":")),
                row.get("quality_status") or "ok", "supplemental", "unverified",
                row.get("license_class"), row.get("analysis_version"),
                row.get("content_fingerprint") or row.get("event_key"),
                float(row.get("reliability_score") or 0),
                float(row.get("reference_value_score") or 0), 0, fetched_at,
                row["_timing"]["available_at"], row["_timing"]["market_session"],
                row["_timing"]["effective_tw_trade_date"],
            )
            for row in data
        ],
    )
    archive_material_news(conn)
    return len(data)


def prune_news_radar_events(conn: sqlite3.Connection, retain_days: int = 30) -> int:
    archive_material_news(conn)
    cursor = conn.execute(
        "DELETE FROM news_radar_event WHERE event_date < date('now', ?)",
        (f"-{max(int(retain_days), 7)} days",),
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


def read_stock_news_radar_events(
    conn: sqlite3.Connection,
    *,
    reference_date: str,
    stock_terms: Iterable[str],
    lookback_days: int = 3,
    limit: int = 8,
    analysis_cutoff: str | None = None,
) -> list[dict[str, Any]]:
    """Map unverified index metadata by exact company or audited taxonomy terms."""

    terms = {str(item).strip().lower() for item in stock_terms if len(str(item).strip()) >= 2}
    effective_date = next_taiwan_trading_date(reference_date, strictly_after=True)
    cutoff = analysis_cutoff_for_reference(
        effective_date,
        explicit_cutoff=analysis_cutoff,
    )
    rows = conn.execute(
        """
        SELECT event_key,event_date,published_at,source_id,publisher,source_url,title,
               affected_terms_json,metrics_json,quality_status,source_quality,
               verification_status,license_class,analysis_version,content_fingerprint,
               reliability_score,reference_value_score,can_override_main_status,fetched_at,
               available_at,market_session,effective_tw_trade_date
        FROM news_radar_event
        WHERE event_date<=?
          AND event_date>=date(?, ?)
          AND quality_status='ok'
          AND source_quality='supplemental'
          AND verification_status='unverified'
          AND effective_tw_trade_date<=?
          AND available_at IS NOT NULL
          AND datetime(available_at)<=datetime(?)
        ORDER BY published_at DESC
        LIMIT 250
        """,
        (
            effective_date,
            reference_date,
            f"-{max(int(lookback_days), 1)} days",
            effective_date,
            cutoff,
        ),
    ).fetchall()
    output: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        affected = _json_list(row.pop("affected_terms_json", "[]"))
        searchable = str(row.get("title") or "").lower()
        taxonomy_matches = {term for term in affected if term.lower() in terms}
        text_matches = {term for term in terms if term in searchable}
        matched = sorted(taxonomy_matches | text_matches)
        if not matched:
            continue
        row["affected_terms"] = affected
        row["matched_stock_terms"] = matched
        row["mapping_method"] = "headline_exact_term" if text_matches else "audited_industry_taxonomy"
        row["metrics"] = _json_object(row.pop("metrics_json", "{}"))
        row["can_override_main_status"] = False
        output.append(row)
        if len(output) >= max(1, min(int(limit), 20)):
            break
    return output
