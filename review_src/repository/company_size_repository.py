from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from core.company_size_schema import ensure_company_size_schema


MAX_COMPANY_SIZE_AGE_DAYS = 45
TPE = ZoneInfo("Asia/Taipei")


def _aware_timestamp(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset")
    return parsed.astimezone(TPE)


def upsert_company_size_snapshots(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    *,
    observed_at: str | None = None,
) -> int:
    ensure_company_size_schema(conn)
    valid = [
        dict(row)
        for row in rows
        if row.get("data_date") and row.get("code") and row.get("market")
    ]
    if not valid:
        return 0
    fetched_at = (
        _aware_timestamp(observed_at, "observed_at").isoformat(timespec="seconds")
        if observed_at is not None
        else datetime.now().astimezone().isoformat(timespec="seconds")
    )
    conn.executemany(
        """
        INSERT INTO official_company_size_snapshot(
            data_date,code,market,paid_in_capital_twd,issued_shares,
            source_id,source_quality,fetched_at
        ) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(data_date,code) DO UPDATE SET
            market=excluded.market,
            paid_in_capital_twd=excluded.paid_in_capital_twd,
            issued_shares=excluded.issued_shares,
            source_id=excluded.source_id,
            fetched_at=excluded.fetched_at
        """,
        [
            (
                row.get("data_date"), row.get("code"), row.get("market"),
                row.get("paid_in_capital_twd"), row.get("issued_shares"),
                row.get("source_id") or "OFFICIAL_COMPANY_OPENAPI", "official",
                fetched_at,
            )
            for row in valid
        ],
    )
    return len(valid)


def read_company_size_rows_at_cutoff(
    conn: sqlite3.Connection,
    *,
    code: str,
    analysis_cutoff: str,
    as_of_date: str | None = None,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Return official point-in-time size rows visible at an explicit cutoff.

    Legacy rows whose retrieval time is missing or offset-naive are excluded. The
    reader never creates the table, repairs timestamps, or substitutes current
    membership for a historical observation.
    """

    cutoff = _aware_timestamp(analysis_cutoff, "analysis_cutoff")
    bounded_limit = max(1, min(int(limit), 32))
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='official_company_size_snapshot'"
    ).fetchone()
    if table is None:
        return []
    parameters: list[Any] = [str(code).zfill(4)]
    date_clause = ""
    if as_of_date is not None:
        try:
            canonical_date = date.fromisoformat(str(as_of_date)).isoformat()
        except ValueError as exc:
            raise ValueError("as_of_date must be an ISO-8601 date") from exc
        date_clause = " AND data_date<=?"
        parameters.append(canonical_date)
    cursor = conn.execute(
        f"""
        SELECT data_date,code,market,paid_in_capital_twd,issued_shares,
               source_id,source_quality,fetched_at
        FROM official_company_size_snapshot
        WHERE code=?{date_clause}
        ORDER BY data_date DESC,fetched_at DESC
        """,
        tuple(parameters),
    )
    columns = [str(column[0]) for column in cursor.description or ()]
    result: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for source in cursor.fetchall():
        row = (
            dict(source)
            if isinstance(source, sqlite3.Row)
            else dict(zip(columns, source, strict=True))
        )
        try:
            observed = _aware_timestamp(row.get("fetched_at"), "fetched_at")
            row_date = date.fromisoformat(str(row.get("data_date") or "")).isoformat()
            issued_shares = float(row.get("issued_shares"))
            paid_in_capital = float(row.get("paid_in_capital_twd"))
        except (TypeError, ValueError):
            continue
        if (
            observed > cutoff
            or row_date in seen_dates
            or str(row.get("source_quality") or "").casefold() != "official"
            or issued_shares <= 0
            or paid_in_capital <= 0
        ):
            continue
        row["data_date"] = row_date
        row["fetched_at"] = observed.isoformat(timespec="seconds")
        seen_dates.add(row_date)
        result.append(row)
        if len(result) >= bounded_limit:
            break
    return result


def read_company_size_context(
    conn: sqlite3.Connection,
    *,
    code: str,
    reference_date: str,
) -> dict[str, Any]:
    exists = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='official_company_size_snapshot'
        """
    ).fetchone()
    if not exists:
        return {"ready": False, "status": "unavailable", "reference_date": reference_date}
    row = conn.execute(
        """
        SELECT data_date,code,market,paid_in_capital_twd,issued_shares,
               source_quality,fetched_at
        FROM official_company_size_snapshot
        WHERE code=? AND data_date<=?
        ORDER BY data_date DESC
        LIMIT 1
        """,
        (code, reference_date),
    ).fetchone()
    if not row:
        return {"ready": False, "status": "unavailable", "reference_date": reference_date}
    item = dict(row)
    try:
        age_days = (date.fromisoformat(reference_date) - date.fromisoformat(item["data_date"])).days
    except (TypeError, ValueError):
        age_days = None
    values_ready = bool(
        item.get("paid_in_capital_twd") is not None
        and float(item["paid_in_capital_twd"]) > 0
        and item.get("issued_shares") is not None
        and float(item["issued_shares"]) > 0
    )
    ready = bool(
        values_ready
        and age_days is not None
        and 0 <= age_days <= MAX_COMPANY_SIZE_AGE_DAYS
        and str(item.get("source_quality") or "").lower() == "official"
    )
    return {
        **item,
        "ready": ready,
        "status": "ok" if ready else "stale" if age_days is not None else "unavailable",
        "reference_date": reference_date,
        "age_days": age_days,
        "maximum_age_days": MAX_COMPANY_SIZE_AGE_DAYS,
    }
