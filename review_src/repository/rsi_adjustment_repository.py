from __future__ import annotations

import sqlite3
from typing import Any

from core.utils import parse_num


def ensure_rsi_adjustment_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rsi_split_adjustment (
            code TEXT NOT NULL,
            event_date TEXT NOT NULL,
            numerator REAL NOT NULL,
            denominator REAL NOT NULL,
            pre_event_factor REAL NOT NULL,
            source TEXT NOT NULL,
            verified_at REAL NOT NULL,
            PRIMARY KEY(code,event_date,source)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rsi_split_adjustment_code_date ON rsi_split_adjustment(code,event_date)"
    )


def upsert_rsi_split_adjustments(
    conn: sqlite3.Connection,
    events: list[dict[str, Any]],
    *,
    verified_at: float,
) -> int:
    ensure_rsi_adjustment_schema(conn)
    written = 0
    for event in events:
        numerator = parse_num(event.get("numerator"))
        denominator = parse_num(event.get("denominator"))
        factor = parse_num(event.get("pre_event_factor"))
        code = str(event.get("code") or "").zfill(4)
        event_date = str(event.get("event_date") or "")
        source = str(event.get("source") or "Yahoo Finance chart split event")
        if not code or not event_date or numerator is None or denominator is None or factor is None:
            continue
        conn.execute(
            """
            INSERT INTO rsi_split_adjustment(
                code,event_date,numerator,denominator,pre_event_factor,source,verified_at
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(code,event_date,source) DO UPDATE SET
                numerator=excluded.numerator,
                denominator=excluded.denominator,
                pre_event_factor=excluded.pre_event_factor,
                verified_at=excluded.verified_at
            """,
            (code, event_date, numerator, denominator, factor, source, verified_at),
        )
        written += 1
    return written


def replace_yahoo_rsi_split_adjustments(
    conn: sqlite3.Connection,
    codes: list[str],
    events: list[dict[str, Any]],
    *,
    verified_at: float,
) -> int:
    """Replace the verified Yahoo adjustment inventory for the audited codes."""

    ensure_rsi_adjustment_schema(conn)
    normalized_codes = sorted({str(code).zfill(4) for code in codes})
    if normalized_codes:
        placeholders = ",".join("?" for _ in normalized_codes)
        conn.execute(
            f"DELETE FROM rsi_split_adjustment WHERE source=? AND code IN ({placeholders})",
            ("Yahoo Finance chart split event", *normalized_codes),
        )
    return upsert_rsi_split_adjustments(conn, events, verified_at=verified_at)


def select_yahoo_applied_split_events(
    local_rows_asc: list[dict[str, Any]],
    yahoo_close_by_date: dict[str, float | None],
    events: list[dict[str, Any]],
    *,
    ratio_tolerance: float = 0.002,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep only split events demonstrably reflected in Yahoo historical closes.

    Yahoo can return a split event without normalizing its quote history.  Events
    are therefore verified newest-to-oldest against the closest common pre-event
    close before they are allowed into the RSI input.
    """

    local_by_date = {
        str(row.get("date") or ""): parse_num(row.get("close"))
        for row in local_rows_asc
        if row.get("date")
    }
    selected: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    later_factor = 1.0

    def close_enough(left: float, right: float) -> bool:
        return abs(left - right) <= ratio_tolerance * max(1.0, abs(right))

    for event in sorted(events, key=lambda item: str(item.get("event_date") or ""), reverse=True):
        event_date = str(event.get("event_date") or "")
        factor = parse_num(event.get("pre_event_factor"))
        common_dates = sorted(
            trade_date
            for trade_date, local_close in local_by_date.items()
            if trade_date < event_date
            and local_close not in (None, 0)
            and yahoo_close_by_date.get(trade_date) is not None
        )
        decision = {"event_date": event_date, "pre_event_factor": factor, "status": "unverified"}
        if not event_date or factor is None or factor <= 0 or not common_dates:
            decision["status"] = "outside_common_history"
            decisions.append(decision)
            continue
        sample_date = common_dates[-1]
        raw_close = float(local_by_date[sample_date])
        yahoo_close = float(yahoo_close_by_date[sample_date])
        observed_ratio = yahoo_close / raw_close
        expected_if_applied = later_factor * float(factor)
        decision.update({
            "sample_date": sample_date,
            "observed_ratio": round(observed_ratio, 9),
            "expected_if_applied": round(expected_if_applied, 9),
            "expected_if_unadjusted": round(later_factor, 9),
        })
        if close_enough(observed_ratio, expected_if_applied):
            selected.append(dict(event))
            later_factor = expected_if_applied
            decision["status"] = "applied_by_yahoo"
        elif close_enough(observed_ratio, later_factor):
            decision["status"] = "event_not_applied_by_yahoo"
        else:
            decision["status"] = "ambiguous_ratio"
        decisions.append(decision)

    selected.sort(key=lambda item: str(item.get("event_date") or ""))
    decisions.sort(key=lambda item: str(item.get("event_date") or ""))
    return selected, decisions


def load_rsi_split_adjustments(conn: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT code,event_date,numerator,denominator,pre_event_factor,source,verified_at
            FROM rsi_split_adjustment
            WHERE code=?
            ORDER BY event_date
            """,
            (str(code).zfill(4),),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [dict(row) for row in rows]


def apply_rsi_split_adjustments(
    rows_asc: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add split-adjusted technical OHLC while preserving official raw OHLC.

    ``rsi_close`` remains as a compatibility alias for ``technical_close``.
    Only split events already verified by ``select_yahoo_applied_split_events``
    are expected here; this function deliberately does not infer adjustments.
    """

    clean_events = []
    for event in events:
        factor = parse_num(event.get("pre_event_factor"))
        event_date = str(event.get("event_date") or "")
        if event_date and factor is not None and factor > 0:
            clean_events.append((event_date, float(factor)))
    out: list[dict[str, Any]] = []
    for source_row in rows_asc:
        row = dict(source_row)
        trade_date = str(row.get("date") or "")
        factor = 1.0
        for event_date, pre_event_factor in clean_events:
            if trade_date < event_date:
                factor *= pre_event_factor
        for field in ("open", "high", "low", "close"):
            raw_value = parse_num(row.get(field))
            row[f"technical_{field}"] = (
                float(raw_value) * factor if raw_value is not None else None
            )
        row["rsi_close"] = row["technical_close"]
        out.append(row)
    return out
