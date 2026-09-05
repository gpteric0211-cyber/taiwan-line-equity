from __future__ import annotations

import re
import time
from contextlib import closing
from datetime import date
from typing import Any, Callable

from adapter.tpex_history import TPEX_TRADING_STOCK_URL, fetch_tpex_stock_month_rows
from core.db import db
from core.market_foundation_schema import upsert_daily_ohlcv_rows
from core.market_session import recent_market_date_for_eod
from core.data_quality import derive_official_monthly_no_bar_evidence
from core.status import set_status
from core.utils import now_tpe
from repository.stock_no_trade_repository import upsert_verified_no_trade_dates
from repository.history_repository import recent_market_reference_dates


def _month_starts(count: int) -> list[str]:
    current = now_tpe().date().replace(day=1)
    out: list[str] = []
    index = current.year * 12 + current.month - 1
    for offset in reversed(range(max(int(count), 1))):
        value = index - offset
        out.append(date(value // 12, value % 12 + 1, 1).isoformat())
    return out


def refresh_tpex_history_codes(
    codes: list[str],
    *,
    months: int = 8,
    fetcher: Callable[..., dict[str, Any]] = fetch_tpex_stock_month_rows,
    sleep_seconds: float = 0.1,
) -> dict[str, Any]:
    """Explicit update path for official TPEx monthly history and no-trade evidence."""

    clean_codes: list[str] = []
    for code in codes:
        raw_code = str(code or "").strip()
        if not re.fullmatch(r"\d{1,4}", raw_code):
            continue
        normalized = raw_code.zfill(4)
        if normalized != "0000" and normalized not in clean_codes:
            clean_codes.append(normalized)
    clean_codes.sort()
    month_targets = _month_starts(months)
    expected_latest_date = recent_market_date_for_eod()
    rows: list[dict[str, Any]] = []
    no_trade_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    successful_reports: list[dict[str, Any]] = []
    observed_dates: dict[str, list[str]] = {code: [] for code in clean_codes}
    total_requests = len(clean_codes) * len(month_targets)
    completed = 0
    for code in clean_codes:
        for month in month_targets:
            try:
                result = fetcher(code, month)
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            completed += 1
            if result.get("ok"):
                fetched_rows = list(result.get("rows") or [])
                fetched_no_trade = list(result.get("verified_no_trade_dates") or [])
                rows.extend(fetched_rows)
                no_trade_rows.extend(fetched_no_trade)
                observed_dates.setdefault(code, []).extend(
                    str(row.get("date")) for row in fetched_rows if row.get("date")
                )
                observed_dates.setdefault(code, []).extend(
                    str(row.get("trade_date")) for row in fetched_no_trade if row.get("trade_date")
                )
                successful_reports.append({
                    "code": code,
                    "month": month[:7],
                    "rows": fetched_rows,
                    "explicit_no_trade_rows": fetched_no_trade,
                })
            else:
                failures.append({
                    "code": code,
                    "month": month[:7],
                    "error": result.get("error") or "official_fetch_failed",
                })
            if sleep_seconds and completed < total_requests:
                time.sleep(float(sleep_seconds))

    if successful_reports:
        with closing(db()) as conn:
            stock_master_exists = bool(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_master'"
            ).fetchone())
            master_rows = (
                {
                    str(row["code"]): dict(row)
                    for row in conn.execute(
                        "SELECT code,first_seen_date,is_active FROM stock_master WHERE code IN ("
                        + ",".join("?" for _ in clean_codes)
                        + ")",
                        clean_codes,
                    ).fetchall()
                }
                if stock_master_exists and clean_codes
                else {}
            )
            reference_dates = recent_market_reference_dates(
                conn,
                max(len(month_targets) * 25, 120),
                latest_completed_date=expected_latest_date,
            )
        inferred: list[dict[str, Any]] = []
        for report in successful_reports:
            master = master_rows.get(str(report["code"])) or {}
            if not bool(master.get("is_active")):
                continue
            inferred.extend(derive_official_monthly_no_bar_evidence(
                code=str(report["code"]),
                market="otc",
                month=str(report["month"]),
                official_rows=list(report["rows"]),
                explicit_no_bar_rows=list(report["explicit_no_trade_rows"]),
                market_reference_dates=reference_dates,
                first_seen_date=master.get("first_seen_date"),
                source="TPEX TRADING_STOCK",
                source_url=TPEX_TRADING_STOCK_URL,
            ))
        known = {
            (str(row.get("code") or ""), str(row.get("trade_date") or ""))
            for row in no_trade_rows
        }
        no_trade_rows.extend(
            row for row in inferred
            if (str(row.get("code") or ""), str(row.get("trade_date") or "")) not in known
        )

    written = 0
    no_trade_written = 0
    if rows or no_trade_rows:
        fetched_at = time.time()
        for row in rows:
            row["updated_at"] = fetched_at
            row["fetched_at"] = fetched_at
        with closing(db()) as conn:
            # Persist authoritative no-trade evidence first so a same-batch
            # fallback/conflicting bar cannot recreate that date.
            no_trade_written = upsert_verified_no_trade_dates(conn, no_trade_rows)
            written = upsert_daily_ohlcv_rows(conn, rows)
            conn.commit()

    latest_official_dates = {
        code: max(dates) if dates else None
        for code, dates in observed_dates.items()
    }
    source_delayed_codes = [
        code
        for code in clean_codes
        if not latest_official_dates.get(code)
        or str(latest_official_dates[code]) < str(expected_latest_date)
    ]
    status = (
        "fresh"
        if clean_codes
        and not failures
        and not source_delayed_codes
        and bool(written or no_trade_written)
        else "stale"
    )
    message = (
        f"TPEx official history: {written} bars, {no_trade_written} verified no-trade dates, "
        f"{len(failures)} failed month requests, {len(source_delayed_codes)} delayed codes "
        f"(expected {expected_latest_date})"
    )
    set_status("tpex_stock_history", status, message)
    return {
        "ok": status == "fresh",
        "codes": clean_codes,
        "months": month_targets,
        "request_count": total_requests,
        "official_rows_fetched": len(rows),
        "official_rows_written": written,
        "official_no_trade_rows_fetched": len(no_trade_rows),
        "official_no_trade_rows_written": no_trade_written,
        "expected_latest_date": expected_latest_date,
        "latest_official_dates": latest_official_dates,
        "source_delayed_codes": source_delayed_codes,
        "failures": failures,
        "writes_db": bool(written or no_trade_written),
    }
