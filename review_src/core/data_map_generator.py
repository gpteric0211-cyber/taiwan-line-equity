from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from core.market_foundation_schema import DAILY_OHLCV_TABLE


MARKET_FOUNDATION_TABLES = [
    "history_price",
    "institution_daily",
    "institution_activity_daily",
    "margin_daily",
    "lending_daily",
    "credit_balance_daily",
    "tdcc_equity_summary",
    "daily_chip_momentum",
    "intraday_quote_snapshot",
    "intraday_quote_1m",
    "price_volume_profile_daily",
    "price_volume_distribution",
    "intraday_time_sales_daily",
    "daily_data_source_audit",
    "twse_daily_valuation",
    "global_market_daily_snapshot",
    "taifex_night_daily_snapshot",
    "official_company_event",
    "estimated_chip_cost_daily",
]


TABLE_PURPOSES = {
    "history_price": "Daily OHLCV / K-line foundation table. This is the official daily OHLCV main table for the project.",
    "institution_daily": "Daily three-institution net buy/sell rows. It is not official position/holding data.",
    "institution_activity_daily": "Official daily foreign, investment-trust, and dealer gross buy, gross sell, and net shares for listed and OTC stocks.",
    "margin_daily": "Daily margin and short balance/change rows.",
    "lending_daily": "Daily securities-lending short-sale balance/change rows; unit is stored explicitly.",
    "credit_balance_daily": "Official listed/OTC daily margin, short, and securities-lending components with dates, units, validation timestamps, and a five-minute usability delay.",
    "tdcc_equity_summary": "TDCC weekly equity concentration summary.",
    "daily_chip_momentum": "Daily derived chip momentum summary built from local data.",
    "intraday_quote_snapshot": "Latest intraday quote snapshot for watchlist/detail support.",
    "intraday_quote_1m": "Intraday one-minute quote rows, retained separately from daily OHLCV.",
    "price_volume_profile_daily": "Existing daily price-volume profile/score input table.",
    "price_volume_distribution": "Real or supplemental price-volume distribution rows by price level.",
    "intraday_time_sales_daily": "Supplemental intraday time-sales detail rows. Non-official unless source says otherwise.",
    "daily_data_source_audit": "Run-level source audit table, retained by started_at for roughly two years.",
    "twse_daily_valuation": "Official TWSE/TPEx valuation table for PE, PB, dividend yield, and valuation date.",
    "global_market_daily_snapshot": "Supplemental persisted US index, sector-ETF, SOX, and TSM ADR close context. Official Taiwan industry codes select the related weighting set; it may adjust confidence but never overrides the Taiwan-stock referee.",
    "taifex_night_daily_snapshot": "Official TAIFEX after-hours futures snapshot. It adjusts next-session confidence and never overrides the stock referee.",
    "official_company_event": "Official listed/OTC daily material events from TWSE and TPEx open-data endpoints.",
    "estimated_chip_cost_daily": "Derived recent incremental institutional cost estimates. These are not actual total institutional holding costs.",
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _indexes(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(f"PRAGMA index_list({table})").fetchall()]


def build_data_file_map(conn: sqlite3.Connection) -> str:
    conn.row_factory = sqlite3.Row
    lines: list[str] = [
        "# Data File Map",
        "",
        "This file is generated from SQLite schema introspection plus a fixed project template.",
        "It documents the latest market-foundation data map only; it is not a runtime migration.",
        "",
        "## Daily OHLCV Decision",
        "",
        f"- Daily OHLCV main table: `{DAILY_OHLCV_TABLE}`.",
        "- Future analysis services should read `history_price` directly for daily OHLCV.",
        "- Do not create a duplicate `daily_price_volume` table while `history_price` owns daily OHLCV semantics.",
        "- OHLCV source priority: `TWSE_OFFICIAL` / `TPEX_OFFICIAL` > `FINMIND` > `YAHOO` / `PCHOME`.",
        "- `INSERT OR REPLACE` must not be used for the OHLCV main table because it can erase source metadata.",
        "",
        "## Source Rules",
        "",
        "- Official sources: TWSE official endpoints, TPEx official endpoints, TDCC official open data, and official valuation feeds.",
        "- Supplemental scraped sources: Yahoo and PChome. These must be marked `SCRAPED`, `PARTIAL`, or `UNAVAILABLE`, never official.",
        "- Yahoo time-sales rows with UNKNOWN, blank, or missing side must be accumulated into `neutral_volume_lots`.",
        "- PChome is currently a skeleton/TODO source and must not perform real HTTP scraping in this phase.",
        "- Institution net buy/sell rows are not official institution holding balances.",
        "",
        "## Retention Rules",
        "",
        "- Daily market-foundation rows retain the latest 600 trading days based on the daily OHLCV table date set.",
        "- `daily_data_source_audit` retains roughly the latest 2 years by `started_at`.",
        "- Cleanup should use batch deletion and `PRAGMA optimize`; routine updates must not auto-run `VACUUM`.",
        "- Future improvement: introduce a formal `market_trading_calendar` so retention cutoff does not depend on OHLCV completeness.",
        "",
        "## Watchlist Intraday TODO",
        "",
        "- Watchlist detail pages should eventually show realtime price/change/volume.",
        "- Sections needing complete same-day analysis should show `台股盤中，暫停顯示` when intraday data is incomplete.",
        "- Related US/industry ETF context and TDCC equity concentration are excluded from that pause rule.",
        "- This phase documents the rule only; it does not change detail-page behavior.",
        "",
        "## Tables",
        "",
    ]

    for table in MARKET_FOUNDATION_TABLES:
        lines.extend([f"### `{table}`", ""])
        lines.append(f"- Purpose: {TABLE_PURPOSES.get(table, 'Project data table.')}")
        if not _table_exists(conn, table):
            lines.extend(["- Status: not present in current DB.", ""])
            continue
        cols = _columns(conn, table)
        idx = _indexes(conn, table)
        lines.append("- Columns:")
        for col in cols:
            pk = " primary_key" if col.get("pk") else ""
            notnull = " not_null" if col.get("notnull") else ""
            lines.append(f"  - `{col.get('name')}` {col.get('type') or ''}{pk}{notnull}".rstrip())
        lines.append("- Indexes:")
        if idx:
            for item in idx:
                unique = " unique" if item.get("unique") else ""
                lines.append(f"  - `{item.get('name')}`{unique}")
        else:
            lines.append("  - none")
        lines.append("")

    lines.extend(
        [
            "## Generated File Notes",
            "",
            "- This file should be regenerated by `review_src/core/data_map_generator.py` after market-foundation schema changes.",
            "- If generation fails, do not publish a partial data map.",
            "",
        ]
    )
    return "\n".join(lines)


def write_data_file_map(conn: sqlite3.Connection, output_path: Path) -> None:
    content = build_data_file_map(conn)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(output_path)
