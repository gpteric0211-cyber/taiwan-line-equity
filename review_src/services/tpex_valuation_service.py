from __future__ import annotations

import re
import time
import math
from contextlib import closing
from datetime import date
from typing import Any, Callable

from adapter.tpex import fetch_tpex_peratio_analysis
from core.db import db
from core.market_session import recent_market_date_for_eod
from core.status import set_status
from repository.twse_valuation_repository import upsert_twse_daily_valuations


MIN_FULL_MARKET_COVERAGE = 0.95


def _valid_metric(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number >= 0


def refresh_tpex_valuation_codes(
    codes: list[str],
    *,
    fetcher: Callable[[], list[dict[str, Any]]] = fetch_tpex_peratio_analysis,
) -> dict[str, Any]:
    """Persist official TPEx PE/PB/yield for explicitly OTC securities."""

    clean_codes = sorted({
        raw.zfill(4)
        for value in codes
        if (raw := str(value or "").strip())
        and re.fullmatch(r"\d{1,4}", raw)
        and raw.zfill(4) != "0000"
    })
    expected_date = recent_market_date_for_eod()
    try:
        fetched = fetcher()
    except Exception as exc:
        message = f"TPEx valuation fetch failed: {exc}"
        set_status("tpex_valuation", "stale", message)
        return {
            "ok": False,
            "status": "failed",
            "codes": clean_codes,
            "expected_date": expected_date,
            "rows_written": 0,
            "missing_codes": clean_codes,
            "source_delayed_codes": clean_codes,
            "error": str(exc),
        }

    wanted = set(clean_codes)
    latest_by_code: dict[str, dict[str, Any]] = {}
    for raw_row in fetched if isinstance(fetched, list) else []:
        row = dict(raw_row) if isinstance(raw_row, dict) else {}
        code = str(row.get("symbol") or "").strip()
        data_date = str(row.get("data_date") or "").strip()
        try:
            valid_date = bool(data_date and date.fromisoformat(data_date))
        except ValueError:
            valid_date = False
        metrics = (row.get("pe_ratio"), row.get("pb_ratio"), row.get("dividend_yield"))
        if (
            code not in wanted
            or not valid_date
            or row.get("source") != "TPEX_PERATIO_ANALYSIS"
            or row.get("source_status") != "ok"
            or not any(_valid_metric(value) for value in metrics)
        ):
            continue
        previous = latest_by_code.get(code)
        if previous is None or data_date > str(previous.get("data_date") or ""):
            latest_by_code[code] = row

    selected = [latest_by_code[code] for code in clean_codes if code in latest_by_code]
    written = 0
    if selected:
        updated_at = time.time()
        with closing(db()) as conn:
            for row in selected:
                code = str(row["symbol"])
                previous = conn.execute(
                    "SELECT eps,eps_source FROM valuation WHERE code=? ORDER BY date DESC LIMIT 1",
                    (code,),
                ).fetchone()
                previous_eps = previous["eps"] if previous and hasattr(previous, "keys") else (previous[0] if previous else None)
                previous_eps_source = previous["eps_source"] if previous and hasattr(previous, "keys") else (previous[1] if previous else None)
                conn.execute(
                    """
                    INSERT INTO valuation(
                        date,code,dividend_yield,pe,pb,source,updated_at,eps,eps_source
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(date,code) DO UPDATE SET
                        dividend_yield=excluded.dividend_yield,
                        pe=excluded.pe,
                        pb=excluded.pb,
                        source=excluded.source,
                        updated_at=excluded.updated_at,
                        eps=COALESCE(valuation.eps,excluded.eps),
                        eps_source=COALESCE(valuation.eps_source,excluded.eps_source)
                    """,
                    (
                        row["data_date"], code, row.get("dividend_yield"),
                        row.get("pe_ratio"), row.get("pb_ratio"),
                        "TPEX_PERATIO_ANALYSIS", updated_at,
                        previous_eps, previous_eps_source,
                    ),
                )
                written += 1
            upsert_twse_daily_valuations(selected, conn=conn)
            conn.commit()

    missing_codes = [code for code in clean_codes if code not in latest_by_code]
    source_delayed_codes = [
        code for code, row in latest_by_code.items()
        if str(row.get("data_date") or "") < str(expected_date)
    ]
    coverage = written / len(clean_codes) if clean_codes else 0.0
    ok = bool(
        clean_codes
        and coverage >= MIN_FULL_MARKET_COVERAGE
        and not source_delayed_codes
        and written == len(selected)
    )
    status = (
        "ok"
        if ok
        else "source_delayed"
        if source_delayed_codes and coverage >= MIN_FULL_MARKET_COVERAGE
        else "partial"
    )
    set_status(
        "tpex_valuation",
        "fresh" if ok else "stale",
        f"TPEx official valuation {written}/{len(clean_codes)}; expected {expected_date}; "
        f"missing {len(missing_codes)}, delayed {len(source_delayed_codes)}",
    )
    return {
        "ok": ok,
        "status": status,
        "codes": clean_codes,
        "expected_date": expected_date,
        "rows_written": written,
        "coverage_pct": round(coverage * 100, 2),
        "minimum_coverage_pct": MIN_FULL_MARKET_COVERAGE * 100,
        "missing_codes": missing_codes,
        "source_delayed_codes": source_delayed_codes,
        "latest_dates": {
            code: row.get("data_date") for code, row in latest_by_code.items()
        },
        "error": None,
    }
