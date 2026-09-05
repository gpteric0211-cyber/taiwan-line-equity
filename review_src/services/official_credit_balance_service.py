from __future__ import annotations
from contextlib import closing

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from adapter.official_credit_balance import (
    fetch_tpex_lending_balance,
    fetch_tpex_margin_balance,
    fetch_twse_lending_balance,
    fetch_twse_margin_balance,
)
from core.config import safe_error
from core.db import db
from repository.credit_balance_repository import (
    prune_credit_balances,
    upsert_official_credit_balances,
)
from repository.market_analytics_repository import active_stock_codes


SourceFetcher = Callable[[str], dict[str, Any]]
MAX_EXCLUDED_FORMULA_ROWS_PER_DATE = 5


def _formula_errors(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for row in rows:
        checks = (
            (
                "margin",
                row.get("margin_balance_lots"),
                None if row.get("margin_prev_balance_lots") is None else (
                    int(row["margin_prev_balance_lots"])
                    + int(row.get("margin_buy_lots") or 0)
                    - int(row.get("margin_sell_lots") or 0)
                    - int(row.get("margin_cash_repayment_lots") or 0)
                ),
            ),
            (
                "short",
                row.get("short_balance_lots"),
                None if row.get("short_prev_balance_lots") is None else (
                    int(row["short_prev_balance_lots"])
                    + int(row.get("short_sell_lots") or 0)
                    - int(row.get("short_buy_lots") or 0)
                    - int(row.get("short_stock_repayment_lots") or 0)
                ),
            ),
            (
                "sbl",
                row.get("sbl_balance_shares"),
                None if row.get("sbl_prev_balance_shares") is None else (
                    int(row["sbl_prev_balance_shares"])
                    + int(row.get("sbl_sell_shares") or 0)
                    - int(row.get("sbl_return_shares") or 0)
                    + int(row.get("sbl_adjust_shares") or 0)
                ),
            ),
        )
        for field, actual, expected in checks:
            if actual is not None and expected is not None and int(actual) != int(expected):
                errors.append({"code": str(row.get("code") or ""), "field": field})
                break
    return errors


def _merge_market_rows(
    margin_rows: list[dict[str, Any]],
    lending_rows: list[dict[str, Any]],
    *,
    allowed_codes: set[str],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in margin_rows:
        code = str(row.get("code") or "")
        if code not in allowed_codes:
            continue
        item = merged.setdefault(code, {
            "trade_date": row.get("trade_date"),
            "code": code,
            "market": row.get("market"),
        })
        item.update(row)
        item["margin_source"] = row.get("source")
        item.pop("source", None)
    for row in lending_rows:
        code = str(row.get("code") or "")
        if code not in allowed_codes:
            continue
        item = merged.setdefault(code, {
            "trade_date": row.get("trade_date"),
            "code": code,
            "market": row.get("market"),
        })
        item.update(row)
        item["lending_source"] = row.get("source")
        item.pop("source", None)
    return list(merged.values())


def refresh_official_credit_balances(
    trade_date: str,
    *,
    dry_run: bool = False,
    fetchers: tuple[SourceFetcher, SourceFetcher, SourceFetcher, SourceFetcher] | None = None,
) -> dict[str, Any]:
    selected_fetchers = fetchers or (
        fetch_twse_margin_balance,
        fetch_twse_lending_balance,
        fetch_tpex_margin_balance,
        fetch_tpex_lending_balance,
    )
    def invoke(fetcher: SourceFetcher) -> dict[str, Any]:
        try:
            return fetcher(trade_date)
        except Exception as exc:
            return {
                "ok": False,
                "source": getattr(fetcher, "__name__", "official_credit_source"),
                "data_date": None,
                "items": [],
                "row_count": 0,
                "error": safe_error(exc),
            }

    with ThreadPoolExecutor(max_workers=len(selected_fetchers)) as executor:
        sources = list(executor.map(invoke, selected_fetchers))
    source_dates = [str(item.get("data_date") or "") for item in sources]
    with closing(db()) as conn, conn:
        listed = set(active_stock_codes(conn, market="listed"))
        otc = set(active_stock_codes(conn, market="otc"))
    rows: list[dict[str, Any]] = []
    market_results: list[dict[str, Any]] = []
    for market, margin_source, lending_source, allowed_codes in (
        ("listed", sources[0], sources[1], listed),
        ("otc", sources[2], sources[3], otc),
    ):
        margin_date = str(margin_source.get("data_date") or "")
        lending_date = str(lending_source.get("data_date") or "")
        pair_ready = bool(
            margin_source.get("ok")
            and lending_source.get("ok")
            and margin_date
            and margin_date == lending_date
        )
        market_rows = (
            _merge_market_rows(
                list(margin_source.get("items") or []),
                list(lending_source.get("items") or []),
                allowed_codes=allowed_codes,
            )
            if pair_ready
            else []
        )
        rows.extend(market_rows)
        market_results.append({
            "market": market,
            "ok": bool(pair_ready and margin_date == trade_date and market_rows),
            "status": (
                "ok"
                if pair_ready and margin_date == trade_date and market_rows
                else ("source_delayed" if pair_ready else "source_unavailable")
            ),
            "data_date": margin_date if pair_ready else None,
            "row_count": len(market_rows),
        })
    invalid = _formula_errors(rows)
    if len(invalid) > MAX_EXCLUDED_FORMULA_ROWS_PER_DATE:
        return {
            "ok": False,
            "status": "invalid",
            "requested_date": trade_date,
            "row_count": len(rows),
            "invalid_formula_rows": len(invalid),
            "invalid_examples": invalid[:10],
            "writes_db": False,
        }
    invalid_codes = {str(value["code"]) for value in invalid}
    valid_rows = [row for row in rows if str(row.get("code") or "") not in invalid_codes]
    written = 0
    pruned = 0
    if not dry_run:
        with closing(db()) as conn, conn:
            written = upsert_official_credit_balances(conn, valid_rows)
            pruned = prune_credit_balances(conn)
            conn.execute("PRAGMA optimize")
            conn.commit()
    requested_date_complete = all(item["ok"] for item in market_results)
    return {
        "ok": requested_date_complete,
        "status": (
            "dry_run"
            if dry_run
            else (
                "ok_with_formula_exclusions"
                if requested_date_complete and invalid
                else ("ok" if requested_date_complete else "source_delayed")
            )
        ),
        "requested_date": trade_date,
        "source_dates": source_dates,
        "source_row_count": len(rows),
        "row_count": len(valid_rows),
        "listed_rows": sum(1 for row in valid_rows if row.get("market") == "listed"),
        "otc_rows": sum(1 for row in valid_rows if row.get("market") == "otc"),
        "excluded_formula_rows": len(invalid),
        "excluded_formula_examples": invalid[:MAX_EXCLUDED_FORMULA_ROWS_PER_DATE],
        "formula_exclusion_policy": (
            "official rows that violate the published balance identity are unavailable; "
            "values are never imputed"
        ),
        "rows_written": written,
        "rows_pruned": pruned,
        "writes_db": not dry_run and written > 0,
        "market_results": market_results,
        "margin_unit": "lots",
        "lending_unit": "shares",
        "usable_from_rule": "max(first_seen_at, validation_passed_at) + 300 seconds",
        "sources": [
            {key: value for key, value in item.items() if key != "items"}
            for item in sources
        ],
    }
