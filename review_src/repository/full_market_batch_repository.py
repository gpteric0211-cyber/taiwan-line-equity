from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from core.data_quality import assess_daily_ohlcv
from core.full_market_batch_schema import FULL_MARKET_BATCH_CONTRACT_VERSION
from core.market_foundation_schema import source_rank


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _codes_hash(codes: set[str]) -> str:
    payload = "\n".join(sorted(codes)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def active_stock_codes_asof(conn: sqlite3.Connection, trade_date: str) -> set[str]:
    """Return the best available active-as-of universe from stock_master.

    Active current rows remain eligible.  A currently inactive row remains in
    a historical universe only through its last observed official-history
    date.  The universe is snapshotted by hash in every batch run.
    """

    rows = conn.execute(
        """
        SELECT code
        FROM stock_master
        WHERE security_type='stock'
          AND (first_seen_date IS NULL OR first_seen_date<=?)
          AND (is_active=1 OR (last_seen_date IS NOT NULL AND last_seen_date>=?))
        """,
        (trade_date, trade_date),
    ).fetchall()
    return {str(row[0]).strip().zfill(4) for row in rows}


def _official_ohlcv_codes(
    conn: sqlite3.Connection,
    trade_date: str,
    expected_codes: set[str],
) -> set[str]:
    rows = conn.execute(
        "SELECT * FROM history_price WHERE date=?",
        (trade_date,),
    ).fetchall()
    valid: set[str] = set()
    for raw in rows:
        row = dict(raw) if hasattr(raw, "keys") else {}
        code = str(row.get("code") or "").strip().zfill(4)
        source_quality = str(row.get("source_quality") or "").strip().lower()
        if (
            code not in expected_codes
            or source_rank(row.get("source")) < 100
            or source_quality not in {"official", "ok", "high"}
        ):
            continue
        if assess_daily_ohlcv(row).get("ready"):
            valid.add(code)
    return valid


def _official_no_trade_codes(
    conn: sqlite3.Connection,
    trade_date: str,
    expected_codes: set[str],
) -> set[str]:
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_no_trade_dates'"
    ).fetchone()
    if not table:
        return set()
    rows = conn.execute(
        """
        SELECT code,market,source,source_url,source_quality
        FROM stock_no_trade_dates
        WHERE trade_date=?
          AND LOWER(source_quality) IN ('official','ok','high')
          AND (
              (
                  LOWER(market)='otc'
                  AND source IN ('TPEX TRADING_STOCK','TPEX DAILY_QUOTES')
                  AND source_url LIKE 'https://www.tpex.org.tw/%'
              )
              OR (
                  LOWER(market)='listed'
                  AND source IN ('TWSE STOCK_DAY','TWSE MI_INDEX')
                  AND (
                      source_url LIKE 'https://www.twse.com.tw/%'
                      OR source_url LIKE 'https://openapi.twse.com.tw/%'
                  )
              )
          )
        """,
        (trade_date,),
    ).fetchall()
    return {
        str(row[0]).strip().zfill(4)
        for row in rows
        if str(row[0]).strip().zfill(4) in expected_codes
    }


def _official_not_applicable_codes(
    conn: sqlite3.Connection,
    trade_date: str,
    expected_codes: set[str],
) -> set[str]:
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='full_market_not_applicable_daily'"
    ).fetchone()
    if not table:
        return set()
    rows = conn.execute(
        """
        SELECT code
        FROM full_market_not_applicable_daily
        WHERE trade_date=?
          AND LOWER(source_quality)='official'
          AND LENGTH(TRIM(source))>0
          AND LENGTH(TRIM(source_url))>0
          AND LENGTH(TRIM(evidence_json))>2
          AND (
              source_url LIKE 'https://www.twse.com.tw/%'
              OR source_url LIKE 'https://openapi.twse.com.tw/%'
              OR source_url LIKE 'https://www.tpex.org.tw/%'
              OR source_url LIKE 'https://mops.twse.com.tw/%'
          )
        """,
        (trade_date,),
    ).fetchall()
    return {
        str(row[0]).strip().zfill(4)
        for row in rows
        if str(row[0]).strip().zfill(4) in expected_codes
    }


def evaluate_full_market_batch(
    conn: sqlite3.Connection,
    trade_date: str,
) -> dict[str, Any]:
    """Evaluate the persisted classification contract without writing."""

    expected = active_stock_codes_asof(conn, trade_date)
    ohlcv = _official_ohlcv_codes(conn, trade_date, expected)
    no_trade = _official_no_trade_codes(conn, trade_date, expected)
    not_applicable = _official_not_applicable_codes(conn, trade_date, expected)
    classified_union = ohlcv | no_trade | not_applicable
    overlaps = (ohlcv & no_trade) | (ohlcv & not_applicable) | (no_trade & not_applicable)
    unclassified = expected - classified_union
    classified_count = len(ohlcv) + len(no_trade) + len(not_applicable)
    complete = bool(
        expected
        and not unclassified
        and not overlaps
        and classified_count == len(expected)
    )
    return {
        "trade_date": trade_date,
        "batch_status": "complete" if complete else "partial",
        "expected_active_asof": len(expected),
        "classified_ohlcv_count": len(ohlcv),
        "official_no_trade_count": len(no_trade),
        "not_applicable_count": len(not_applicable),
        "classified_count": classified_count,
        "unclassified_count": len(unclassified),
        "overlap_count": len(overlaps),
        "expected_universe_hash": _codes_hash(expected),
        "classified_universe_hash": _codes_hash(classified_union),
        "unclassified_codes": sorted(unclassified),
        "overlap_codes": sorted(overlaps),
        "contract_version": FULL_MARKET_BATCH_CONTRACT_VERSION,
    }


def record_full_market_batch(
    conn: sqlite3.Connection,
    evaluation: dict[str, Any],
    *,
    storage_status: str,
    source_summary: list[dict[str, Any]] | None = None,
    run_id: str | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    """Record an audited run and atomically publish it only when complete.

    The caller owns the surrounding transaction.  This function never commits
    and never creates schema, so data rows and the publication marker can share
    one transaction boundary.
    """

    current_run_id = run_id or uuid.uuid4().hex
    now_text = _utc_now_text()
    values = {
        **evaluation,
        "run_id": current_run_id,
        "storage_status": storage_status,
        "source_summary_json": json.dumps(
            source_summary or [], ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ),
        "unclassified_codes_json": json.dumps(
            evaluation.get("unclassified_codes") or [], ensure_ascii=False, separators=(",", ":")
        ),
        "overlap_codes_json": json.dumps(
            evaluation.get("overlap_codes") or [], ensure_ascii=False, separators=(",", ":")
        ),
        "started_at": started_at or now_text,
        "finalized_at": now_text,
    }
    conn.execute(
        """
        INSERT INTO full_market_batch_runs(
            run_id,trade_date,batch_status,storage_status,
            expected_active_asof,classified_ohlcv_count,
            official_no_trade_count,not_applicable_count,classified_count,
            unclassified_count,overlap_count,expected_universe_hash,
            classified_universe_hash,unclassified_codes_json,overlap_codes_json,
            source_summary_json,contract_version,started_at,finalized_at
        ) VALUES(
            :run_id,:trade_date,:batch_status,:storage_status,
            :expected_active_asof,:classified_ohlcv_count,
            :official_no_trade_count,:not_applicable_count,:classified_count,
            :unclassified_count,:overlap_count,:expected_universe_hash,
            :classified_universe_hash,:unclassified_codes_json,:overlap_codes_json,
            :source_summary_json,:contract_version,:started_at,:finalized_at
        )
        """,
        values,
    )
    if evaluation.get("batch_status") == "complete":
        conn.execute(
            """
            INSERT INTO full_market_batch_publications(
                trade_date,run_id,expected_active_asof,classified_ohlcv_count,
                official_no_trade_count,not_applicable_count,
                expected_universe_hash,classified_universe_hash,
                contract_version,published_at
            ) VALUES(
                :trade_date,:run_id,:expected_active_asof,:classified_ohlcv_count,
                :official_no_trade_count,:not_applicable_count,
                :expected_universe_hash,:classified_universe_hash,
                :contract_version,:finalized_at
            )
            ON CONFLICT(trade_date) DO UPDATE SET
                run_id=excluded.run_id,
                expected_active_asof=excluded.expected_active_asof,
                classified_ohlcv_count=excluded.classified_ohlcv_count,
                official_no_trade_count=excluded.official_no_trade_count,
                not_applicable_count=excluded.not_applicable_count,
                expected_universe_hash=excluded.expected_universe_hash,
                classified_universe_hash=excluded.classified_universe_hash,
                contract_version=excluded.contract_version,
                published_at=excluded.published_at
            """,
            values,
        )
    return {**evaluation, "run_id": current_run_id, "storage_status": storage_status}


def latest_published_full_market_date(conn: sqlite3.Connection) -> str | None:
    """Read the explicit publication marker; never infer completion from rows."""

    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='full_market_batch_publications'"
    ).fetchone()
    if not table:
        return None
    row = conn.execute(
        "SELECT MAX(trade_date) FROM full_market_batch_publications"
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def resolve_full_market_analysis_date(
    conn: sqlite3.Connection,
    requested_date: str | None = None,
) -> str | None:
    """Resolve the as-of date for a read-only full-market analysis.

    An explicitly requested historical date remains authoritative.  Current
    close-batch analysis must use only the atomically published full-market
    marker and must never infer recency from a stock-local ``MAX(date)``.
    """

    explicit = str(requested_date or "").strip()
    if explicit:
        return explicit
    return latest_published_full_market_date(conn)
