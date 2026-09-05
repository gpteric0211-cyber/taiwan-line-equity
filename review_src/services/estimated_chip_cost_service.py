from __future__ import annotations
from contextlib import closing

from typing import Any

from analysis.estimated_chip_cost import (
    FORMULA_VERSION,
    build_canonical_cost_snapshot as _build_canonical_cost_snapshot,
    calculate_institution_estimated_cost,
)
from core.db import db
from repository.full_market_batch_repository import resolve_full_market_analysis_date
from repository.estimated_chip_cost_repository import (
    ensure_estimated_chip_cost_schema,
    list_history_codes,
    load_cost_input_rows,
    prune_estimated_chip_cost,
    read_canonical_estimated_cost_rows,
    upsert_estimated_chip_cost_rows,
)


def build_canonical_cost_snapshot(
    rows: list[dict[str, Any]],
    *,
    expected_date: str | None,
) -> dict[str, Any]:
    return _build_canonical_cost_snapshot(rows, expected_date)


def get_canonical_cost_snapshot(
    code: str,
    *,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    with closing(db()) as conn, conn:
        trade_date = resolve_full_market_analysis_date(conn, as_of_date)
        rows = (
            read_canonical_estimated_cost_rows(
                conn,
                code=code,
                trade_date=trade_date,
            )
            if trade_date
            else []
        )
    return build_canonical_cost_snapshot(rows, expected_date=trade_date)


def refresh_estimated_chip_costs(
    *,
    codes: list[str] | None = None,
    days: int = 240,
    as_of_date: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    input_count = 0
    generated_count = 0
    valid_count = 0
    valid_by_type: dict[str, int] = {}
    publishable_count = 0
    publishable_by_type: dict[str, int] = {}
    written = 0
    pruned = 0
    target_date: str | None = None
    with closing(db()) as conn, conn:
        target_date = resolve_full_market_analysis_date(conn, as_of_date)
        selected_codes = sorted(set(codes or list_history_codes(conn)))
        target_code_count = 0
        if target_date:
            target_code_count = int(
                conn.execute(
                    """
                    SELECT COUNT(DISTINCT code)
                    FROM history_price
                    WHERE date=? AND close IS NOT NULL AND volume IS NOT NULL AND volume>0
                    """,
                    (target_date,),
                ).fetchone()[0]
                or 0
            )
        if not dry_run:
            ensure_estimated_chip_cost_schema(conn)
        for offset in range(0, len(selected_codes), 50):
            batch_codes = selected_codes[offset : offset + 50]
            inputs = load_cost_input_rows(conn, batch_codes, days, as_of_date=target_date)
            input_count += len(inputs)
            calculated = [
                *calculate_institution_estimated_cost(inputs, "foreign_estimated"),
                *calculate_institution_estimated_cost(inputs, "trust_estimated"),
            ]
            generated_count += len(calculated)
            rows = [
                row for row in calculated
                if str(row.get("trade_date") or "") == str(target_date or "")
            ]
            for row in rows:
                if row.get("cost_status") in {"estimated", "ok", "proxy_only"} and row.get("estimated_cost") is not None:
                    valid_count += 1
                    cost_type = str(row.get("cost_type") or "")
                    valid_by_type[cost_type] = valid_by_type.get(cost_type, 0) + 1
                    if str(row.get("confidence") or "").lower() == "medium":
                        publishable_count += 1
                        publishable_by_type[cost_type] = publishable_by_type.get(cost_type, 0) + 1
            if not dry_run:
                written += upsert_estimated_chip_cost_rows(conn, rows)
        if not dry_run:
            pruned = prune_estimated_chip_cost(conn, selected_codes, keep_days=720)
            conn.execute("PRAGMA optimize")
            conn.commit()
    return {
        "ok": bool(target_date and input_count > 0 and (dry_run or written == target_code_count * 2)),
        "status": "dry_run" if dry_run else "ok",
        "writes_db": not dry_run and written > 0,
        "codes": len(selected_codes),
        "target_traded_codes": target_code_count,
        "input_rows": input_count,
        "generated_rows": generated_count,
        "valid_rows": valid_count,
        "valid_by_type": valid_by_type,
        "publishable_rows": publishable_count,
        "publishable_by_type": publishable_by_type,
        "rows_written": written,
        "rows_pruned": pruned,
        "trade_date": target_date,
        "formula_version": FORMULA_VERSION,
        "source_mode": "official_only",
        "scope_note": "240-session recent incremental-position estimate, not actual total institutional holding cost",
    }
