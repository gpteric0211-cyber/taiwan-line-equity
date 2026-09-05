from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from typing import Any

from core.db import db
from core.data_quality import assess_daily_ohlcv, assess_recent_trading_date_coverage
from core.utils import parse_num, today_iso
from repository.full_market_batch_repository import resolve_full_market_analysis_date
from repository.stock_no_trade_repository import verified_no_trade_dates


def latest_history_dates(
    code: str,
    limit: int = 260,
    *,
    as_of_date: str | None = None,
) -> list[sqlite3.Row]:
    with closing(db()) as conn:
        selected_date = resolve_full_market_analysis_date(conn, as_of_date)
        if not selected_date:
            return []
        return list(
            conn.execute(
                """
                SELECT *
                FROM history_price
                WHERE code=? AND date<=?
                ORDER BY date DESC
                LIMIT ?
                """,
                (code, selected_date, limit),
            )
        )


def normalize_history_row_volume(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """Return a history row with volume normalized to canonical shares."""
    out = dict(row)
    unit = str(out.get("volume_unit") or "shares").strip().lower()
    vol = parse_num(out.get("volume"))
    if vol is not None and unit == "lots":
        out["volume"] = vol * 1000
        out["volume_unit"] = "shares"
    return out


def history_rows_asc(
    code: str,
    limit: int = 260,
    *,
    as_of_date: str | None = None,
) -> list[dict[str, Any]]:
    rows = latest_history_dates(code, limit=limit, as_of_date=as_of_date)
    normalized = [normalize_history_row_volume(r) for r in reversed(rows)]
    return [row for row in normalized if assess_daily_ohlcv(row)["ready"]]


def recent_market_reference_dates(
    conn: sqlite3.Connection,
    required_days: int = 30,
    *,
    latest_completed_date: str | None = None,
) -> list[str]:
    """Return the strict completed-market date inventory.

    Any observed completed-day row is evidence of a real trading date and must
    not be hidden merely because most stocks are missing it. The expected
    latest completed trading date is always included so stale data fails closed.
    """

    cutoff = resolve_full_market_analysis_date(conn, latest_completed_date)
    if not cutoff:
        return []
    limit = max(int(required_days), 1)
    rows = conn.execute(
        """
        SELECT DISTINCT date
        FROM history_price
        WHERE date IS NOT NULL AND TRIM(date)<>'' AND close IS NOT NULL AND close>0
          AND date<=?
        ORDER BY date DESC
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()
    observed = [str(row["date"] if isinstance(row, sqlite3.Row) else row[0]) for row in rows]
    return [cutoff, *[trade_date for trade_date in observed if trade_date != cutoff]][:limit]


def history_date_coverage(
    conn: sqlite3.Connection,
    code: str,
    *,
    required_days: int = 30,
    reference_dates: list[str] | None = None,
) -> dict[str, Any]:
    required = max(int(required_days), 1)
    supplied_reference = sorted(
        {str(value) for value in (reference_dates or []) if value},
        reverse=True,
    )
    reference = supplied_reference or recent_market_reference_dates(conn, required)
    if not reference:
        return assess_recent_trading_date_coverage([], [], required_days=required)

    # A market can be open while one security has an official no-trade row
    # (for example, a suspension).  Expand the market window and remove only
    # explicitly persisted TWSE/TPEx evidence; an unexplained gap still fails
    # closed exactly as before.
    latest_reference = reference[0]
    candidate_count = required
    candidates = list(reference)
    exceptions: set[str] = set()
    maximum_candidates = max(required * 8, 2000)
    previous_state: tuple[int, int] | None = None
    for _ in range(10):
        expanded = recent_market_reference_dates(
            conn,
            candidate_count,
            latest_completed_date=latest_reference,
        )
        candidates = sorted(set(candidates).union(expanded), reverse=True)
        exceptions = verified_no_trade_dates(conn, code, candidates)
        effective = [trade_date for trade_date in candidates if trade_date not in exceptions]
        if len(effective) >= required:
            reference = effective[:required]
            break
        state = (len(candidates), len(effective))
        if state == previous_state and candidate_count >= maximum_candidates:
            break
        previous_state = state
        deficit = required - len(effective)
        candidate_count = min(
            max(candidate_count * 2, candidate_count + deficit + len(exceptions)),
            maximum_candidates,
        )
    else:
        reference = [trade_date for trade_date in candidates if trade_date not in exceptions][:required]

    placeholders = ",".join("?" for _ in reference)
    rows = conn.execute(
        f"""
        SELECT date,code,open,high,low,close,volume
        FROM history_price
        WHERE code=? AND date IN ({placeholders})
        """,
        (str(code).zfill(4), *reference),
    ).fetchall()
    actual = [
        str(row["date"] if isinstance(row, sqlite3.Row) else row[0])
        for row in rows
        if assess_daily_ohlcv(dict(row) if isinstance(row, sqlite3.Row) else {
            "date": row[0], "code": row[1], "open": row[2], "high": row[3],
            "low": row[4], "close": row[5], "volume": row[6],
        })["ready"]
    ]
    result = assess_recent_trading_date_coverage(actual, reference, required_days=required)
    used_floor = reference[-1] if reference else None
    used_exceptions = sorted(
        (
            trade_date
            for trade_date in exceptions
            if not used_floor or trade_date >= used_floor
        ),
        reverse=True,
    )
    result["verified_no_trade_dates"] = used_exceptions
    result["market_reference_days_considered"] = len(reference) + len(used_exceptions)
    conflicting_dates: list[str] = []
    if used_exceptions:
        conflict_placeholders = ",".join("?" for _ in used_exceptions)
        conflict_rows = conn.execute(
            f"""
            SELECT date,code,open,high,low,close,volume
            FROM history_price
            WHERE code=? AND date IN ({conflict_placeholders})
            """,
            (str(code).zfill(4), *used_exceptions),
        ).fetchall()
        conflicting_dates = sorted({
            str(row["date"] if isinstance(row, sqlite3.Row) else row[0])
            for row in conflict_rows
            if assess_daily_ohlcv(dict(row) if isinstance(row, sqlite3.Row) else {
                "date": row[0], "code": row[1], "open": row[2], "high": row[3],
                "low": row[4], "close": row[5], "volume": row[6],
            })["ready"]
        }, reverse=True)
    result["no_trade_history_conflicts"] = conflicting_dates
    if conflicting_dates:
        result["ready"] = False
        result["status"] = "unavailable"
        result["reason"] = "history_no_trade_conflict"
        return result
    if result.get("ready") and used_exceptions:
        result["reason"] = "ok_with_verified_no_trade_dates"
    return result


def _date_from_iso(d: str) -> date | None:
    try:
        return datetime.fromisoformat(str(d)[:10]).date()
    except Exception:
        return None


def _trading_dates_around(code: str, current_date: str, days_before: int, days_after: int) -> dict[str, int]:
    # Map nearby history_price dates to trading-day offsets from current_date.
    dates: list[str] = []
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM history_price WHERE code=? ORDER BY date DESC LIMIT 260",
            (str(code).zfill(4),),
        ).fetchall()
        dates = sorted([r['date'] for r in rows if r['date']])
    if current_date not in dates:
        dates.append(current_date)
        dates = sorted(set(dates))
    try:
        cur_idx = dates.index(current_date)
    except ValueError:
        return {}
    out = {}
    for idx, d in enumerate(dates):
        diff = idx - cur_idx
        if -days_before <= diff <= days_after:
            out[d] = diff
    return out


def get_recent_corporate_action(code: str, current_date: str | None, days_before: int = 1, days_after: int = 5) -> dict[str, Any] | None:
    if not current_date:
        current_date = today_iso()
    mapping = _trading_dates_around(code, current_date, days_before, days_after)
    # If no history mapping exists, fallback to calendar days.
    if not mapping:
        cur = _date_from_iso(current_date)
        if not cur:
            return None
        for i in range(-days_before, days_after + 1):
            mapping[(cur + timedelta(days=i)).isoformat()] = i
    dates = list(mapping.keys())
    if not dates:
        return None
    qmarks = ','.join('?' for _ in dates)
    with closing(db()) as conn:
        rows = conn.execute(
            f"SELECT * FROM corporate_actions WHERE code=? AND date IN ({qmarks}) ORDER BY is_confirmed DESC, date DESC",
            [str(code).zfill(4), *dates],
        ).fetchall()
    if not rows:
        return None
    r = dict(rows[0])
    r['has_recent_action'] = True
    r['days_from_action'] = -int(mapping.get(r['date'], 0))  # current - action: negative means upcoming, positive means after
    # Actually mapping stored action_index - current_index, so invert sign makes: action tomorrow -> -1, action before -> +1.
    return r
