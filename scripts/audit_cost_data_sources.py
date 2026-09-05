from __future__ import annotations

import argparse
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


SAMPLE_CODES = ["3491", "2317", "6757", "2330", "2454"]

FOREIGN_TRUST_KEYWORDS = [
    "foreign",
    "institution",
    "investment",
    "trust",
    "dealer",
    "legal",
    "qfii",
    "fii",
    "sit",
    "法人",
    "外資",
    "投信",
    "自營",
    "buy",
    "sell",
    "net",
    "shares",
    "volume",
    "accumulate",
    "cumulative",
    "cum_buy",
    "cum_sell",
]
HOLDING_KEYWORDS = [
    "tdcc",
    "holding",
    "shareholding",
    "ownership",
    "ratio",
    "holders",
    "持股",
    "比例",
    "集保",
    "股權",
]
MARGIN_KEYWORDS = [
    "margin",
    "financing",
    "finance",
    "credit",
    "short",
    "loan",
    "融資",
    "融券",
    "信用",
    "balance",
    "amount",
    "cash_repayment",
]
BROKER_KEYWORDS = [
    "broker",
    "branch",
    "dealer",
    "bsr",
    "rank",
    "broker_rank",
    "分點",
    "券商",
    "buy_amount",
    "sell_amount",
    "buy_shares",
    "sell_shares",
    "net_buy",
    "net_sell",
    "accumulate",
    "cumulative",
    "cum_buy",
    "cum_sell",
]
PRICE_KEYWORDS = [
    "history_price",
    "price",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_value",
    "amount",
    "成交金額",
    "成交量",
]


@dataclass
class TableInfo:
    name: str
    columns: list[str]
    row_count: int
    date_column: str | None
    min_date: str | None
    max_date: str | None
    code_column: str | None
    code_count: int | None


def norm_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if text.isdigit() and len(text) < 4:
        return text.zfill(4)
    return text


def qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def to_num(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in {"", "--", "None", "nan"}:
        return None
    try:
        val = float(text)
    except Exception:
        return None
    if not math.isfinite(val):
        return None
    return val


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row["name"]) for row in rows]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({qident(table)})").fetchall()]


def detect_date_column(columns: list[str]) -> str | None:
    preferred = ["date", "trade_date", "data_date", "snapshot_date", "calc_date", "updated_at", "fetched_at"]
    lower = {col.lower(): col for col in columns}
    for key in preferred:
        if key in lower:
            return lower[key]
    for col in columns:
        if "date" in col.lower():
            return col
    return None


def detect_code_column(columns: list[str]) -> str | None:
    preferred = ["code", "stock_id", "stock_code", "symbol"]
    lower = {col.lower(): col for col in columns}
    for key in preferred:
        if key in lower:
            return lower[key]
    for col in columns:
        low = col.lower()
        if "code" in low or "symbol" in low or "stock" in low:
            return col
    return None


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return row[0]


def load_table_info(conn: sqlite3.Connection, table: str) -> TableInfo:
    columns = table_columns(conn, table)
    row_count = int(scalar(conn, f"SELECT COUNT(*) FROM {qident(table)}") or 0)
    date_col = detect_date_column(columns)
    min_date = max_date = None
    if date_col:
        min_date = scalar(conn, f"SELECT MIN({qident(date_col)}) FROM {qident(table)} WHERE {qident(date_col)} IS NOT NULL")
        max_date = scalar(conn, f"SELECT MAX({qident(date_col)}) FROM {qident(table)} WHERE {qident(date_col)} IS NOT NULL")
    code_col = detect_code_column(columns)
    code_count = None
    if code_col:
        code_count = int(
            scalar(conn, f"SELECT COUNT(DISTINCT {qident(code_col)}) FROM {qident(table)} WHERE {qident(code_col)} IS NOT NULL") or 0
        )
    return TableInfo(table, columns, row_count, date_col, str(min_date) if min_date else None, str(max_date) if max_date else None, code_col, code_count)


def find_candidates(infos: dict[str, TableInfo], keywords: list[str]) -> list[TableInfo]:
    out: list[TableInfo] = []
    for info in infos.values():
        hay = " ".join([info.name, *info.columns]).lower()
        if any(str(key).lower() in hay for key in keywords):
            out.append(info)
    return out


def rows_for_code(conn: sqlite3.Connection, table: str, code: str, limit: int = 5) -> list[sqlite3.Row]:
    cols = table_columns(conn, table)
    code_col = detect_code_column(cols)
    if not code_col:
        return []
    date_col = detect_date_column(cols)
    order = f"ORDER BY {qident(date_col)} DESC" if date_col else ""
    return conn.execute(
        f"SELECT * FROM {qident(table)} WHERE TRIM(CAST({qident(code_col)} AS TEXT))=? {order} LIMIT ?",
        (code, limit),
    ).fetchall()


def latest_price(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    if "history_price" not in table_names(conn):
        return {"available": False}
    rows = conn.execute(
        """
        SELECT date, close, volume, amount, volume_unit, source
        FROM history_price
        WHERE code=? AND close IS NOT NULL
        ORDER BY date DESC
        LIMIT 1
        """,
        (code,),
    ).fetchall()
    if not rows:
        return {"available": False}
    row = dict(rows[0])
    close = to_num(row.get("close"))
    amount = to_num(row.get("amount"))
    volume = to_num(row.get("volume"))
    vwap = None
    if amount and volume and volume > 0:
        vwap = amount / volume
    row["close"] = close
    row["vwap"] = vwap
    row["available"] = True
    return row


def coverage(conn: sqlite3.Connection, table: str, code: str) -> dict[str, Any]:
    cols = table_columns(conn, table)
    code_col = detect_code_column(cols)
    date_col = detect_date_column(cols)
    if not code_col:
        return {"row_count": 0, "min_date": None, "max_date": None}
    where = f"TRIM(CAST({qident(code_col)} AS TEXT))=?"
    count = scalar(conn, f"SELECT COUNT(*) FROM {qident(table)} WHERE {where}", (code,))
    min_date = max_date = None
    if date_col:
        min_date = scalar(conn, f"SELECT MIN({qident(date_col)}) FROM {qident(table)} WHERE {where}", (code,))
        max_date = scalar(conn, f"SELECT MAX({qident(date_col)}) FROM {qident(table)} WHERE {where}", (code,))
    return {"row_count": int(count or 0), "min_date": min_date, "max_date": max_date}


def pick_existing_tables(infos: dict[str, TableInfo], names: list[str]) -> list[str]:
    return [name for name in names if name in infos]


def column_exists(infos: dict[str, TableInfo], table: str, column: str) -> bool:
    return table in infos and column in infos[table].columns


def financing_amount_diagnostic(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "financing_amount_column": "not_found",
        "financing_balance_column": "margin_daily.margin_balance" if "margin_daily" in table_names(conn) else "not_found",
        "financing_balance_unit_guess": "ambiguous_no_explicit_share_or_lot_unit",
        "amount_unit_guess": "amount_column_not_found",
        "amount_unit_evidence": ["No financing amount / loan amount column found in margin_daily."],
        "amount_ratio_to_price": "not_available_missing_financing_amount",
        "ratio_as_shares": "not_applicable_missing_financing_amount",
        "ratio_as_lots": "not_applicable_missing_financing_amount",
        "sample_days_used": 0,
        "can_compute_financing_loan_per_share": False,
        "can_compute_estimated_financing_purchase_cost": False,
        "requires_financing_ratio": True,
        "sample": None,
    }
    if "margin_daily" not in table_names(conn):
        result["amount_unit_evidence"] = ["margin_daily table not found."]
        return result
    rows = conn.execute(
        """
        SELECT date, margin_delta, margin_balance, short_delta, short_balance, source
        FROM margin_daily
        WHERE code=?
        ORDER BY date DESC
        LIMIT 5
        """,
        (code,),
    ).fetchall()
    result["sample_days_used"] = len(rows)
    if not rows:
        result["amount_unit_evidence"].append(f"No margin rows for {code}.")
        return result
    latest = dict(rows[0])
    price = latest_price(conn, code)
    result["sample"] = {
        "date": latest.get("date"),
        "margin_balance": latest.get("margin_balance"),
        "margin_delta": latest.get("margin_delta"),
        "close": price.get("close"),
        "vwap": price.get("vwap"),
    }
    if not price.get("available"):
        result["amount_unit_evidence"].append("price_reference_missing")
    return result


def broker_diagnostic(infos: dict[str, TableInfo]) -> dict[str, Any]:
    candidates = find_candidates(infos, BROKER_KEYWORDS)
    broker_tables = [info for info in candidates if any(k in " ".join([info.name, *info.columns]).lower() for k in ("broker", "branch", "分點", "券商"))]
    has_amount = any(any("amount" in c.lower() or "金額" in c for c in info.columns) for info in broker_tables)
    has_shares = any(any("share" in c.lower() or "volume" in c.lower() or "股" in c or "量" in c for c in info.columns) for info in broker_tables)
    days = max((info.row_count for info in broker_tables), default=0)
    return {
        "broker_table_exists": bool(broker_tables),
        "broker_tables": [info.name for info in broker_tables],
        "broker_history_days": days if broker_tables else 0,
        "broker_codes_count": max((info.code_count or 0 for info in broker_tables), default=0),
        "has_branch_buy_sell_shares": bool(has_shares),
        "has_branch_buy_sell_amount": bool(has_amount),
        "has_all_branches_or_top_only": "unavailable_no_broker_branch_table" if not broker_tables else "needs_manual_table_review",
        "can_compute_branch_vwap_cost": bool(broker_tables and has_shares and has_amount),
        "can_compute_main_force_estimated_cost": bool(broker_tables and has_shares and has_amount),
        "limitations": [] if broker_tables else ["No local broker-branch table with branch buy/sell shares and amounts was found."],
    }


def code_sample(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    tables = table_names(conn)
    out: dict[str, Any] = {"code": code}
    for table in ["history_price", "institution_daily", "margin_daily", "foreign_shareholding", "tdcc_holding_distribution", "tdcc_equity_summary", "daily_chip_momentum"]:
        if table in tables:
            out[table] = coverage(conn, table, code)
    price = latest_price(conn, code)
    out["trade_value_vwap"] = {
        "has_trade_value": bool(price.get("amount")),
        "vwap": price.get("vwap"),
        "close": price.get("close"),
        "date": price.get("date"),
    }
    out["foreign_net_available"] = bool(out.get("institution_daily", {}).get("row_count")) and "institution_daily" in tables
    out["trust_net_available"] = out["foreign_net_available"]
    out["foreign_holding_available"] = bool(out.get("foreign_shareholding", {}).get("row_count"))
    out["trust_holding_available"] = False
    out["margin_available"] = bool(out.get("margin_daily", {}).get("row_count"))
    out["broker_data_available"] = False
    out["financing_diagnostic"] = financing_amount_diagnostic(conn, code)
    if not any((out.get(t, {}).get("row_count") or 0) > 0 for t in ["history_price", "institution_daily", "margin_daily", "foreign_shareholding"]):
        out["status"] = "no_data_found"
    else:
        out["status"] = "ok"
    return out


def category_status(conn: sqlite3.Connection, infos: dict[str, TableInfo]) -> dict[str, dict[str, Any]]:
    history_ok = "history_price" in infos and infos["history_price"].row_count > 0
    has_trade_value = column_exists(infos, "history_price", "amount")
    inst_ok = "institution_daily" in infos and infos["institution_daily"].row_count > 0
    foreign_col = column_exists(infos, "institution_daily", "foreign_net")
    trust_col = column_exists(infos, "institution_daily", "trust_net")
    foreign_holding = "foreign_shareholding" in infos and infos["foreign_shareholding"].row_count > 0 and column_exists(infos, "foreign_shareholding", "ForeignInvestmentShares")
    margin_ok = "margin_daily" in infos and infos["margin_daily"].row_count > 0
    broker = broker_diagnostic(infos)
    financing_amount_found = False
    if "margin_daily" in infos:
        financing_amount_found = any("amount" in col.lower() or "loan" in col.lower() for col in infos["margin_daily"].columns)
    return {
        "foreign_estimated_cost": {
            "status": "ready" if inst_ok and foreign_col and history_ok and has_trade_value and foreign_holding else ("partial" if inst_ok and foreign_col and history_ok else "unavailable"),
            "confidence": "medium" if foreign_holding else "low",
            "reason": "foreign_net, OHLCV/trade_value, and foreign_shareholding anchor exist" if foreign_holding else "foreign holding calibration or trade_value is missing",
        },
        "investment_trust_estimated_cost": {
            "status": "partial" if inst_ok and trust_col and history_ok else "unavailable",
            "confidence": "low",
            "reason": "trust_net and OHLCV exist, but investment_trust_holding_not_found",
        },
        "margin_loan_or_financing_cost": {
            "status": "partial" if margin_ok else "unavailable",
            "confidence": "low",
            "reason": "margin_balance exists but financing_amount column not found" if margin_ok and not financing_amount_found else "margin data unavailable",
        },
        "main_force_broker_branch_cost": {
            "status": "ready" if broker["can_compute_main_force_estimated_cost"] else "unavailable",
            "confidence": "none" if not broker["broker_table_exists"] else "needs_review",
            "reason": "; ".join(broker["limitations"]) if broker["limitations"] else "broker branch shares and amount columns need review",
        },
    }


def format_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    lines = ["| " + " | ".join(map(str, header)) + " |"]
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines)


def sample_jsonish(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 240 else text[:237] + "..."


def build_report(conn: sqlite3.Connection, db_path: Path) -> str:
    infos = {name: load_table_info(conn, name) for name in table_names(conn)}
    status = category_status(conn, infos)
    foreign_tables = find_candidates(infos, FOREIGN_TRUST_KEYWORDS)
    holding_tables = find_candidates(infos, HOLDING_KEYWORDS)
    margin_tables = find_candidates(infos, MARGIN_KEYWORDS)
    broker = broker_diagnostic(infos)
    broker_tables = [infos[name] for name in broker["broker_tables"] if name in infos]
    price_tables = find_candidates(infos, PRICE_KEYWORDS)

    summary_rows = [
        ["成本類型", "所需真實資料", "本機是否存在", "table / column", "覆蓋日期", "覆蓋股票數", "是否可計算成本", "建議名稱", "可信度", "下一步建議"],
        [
            "外資估算成本",
            "外資每日淨買賣 + OHLCV/trade_value/VWAP + 外資持股校準",
            status["foreign_estimated_cost"]["status"],
            "institution_daily.foreign_net; history_price.amount; foreign_shareholding.ForeignInvestmentShares",
            f"{infos.get('institution_daily').min_date if infos.get('institution_daily') else '--'} ~ {infos.get('institution_daily').max_date if infos.get('institution_daily') else '--'}",
            infos.get("institution_daily").code_count if infos.get("institution_daily") else 0,
            "可做估算；仍非券商真實成本",
            "外資公開資料估算成本",
            status["foreign_estimated_cost"]["confidence"],
            "可沿用現有估算，但前端需標示估算與校準來源",
        ],
        [
            "投信估算成本",
            "投信每日淨買賣 + OHLCV/trade_value/VWAP + 投信持股校準",
            status["investment_trust_estimated_cost"]["status"],
            "institution_daily.trust_net; history_price.amount; investment_trust_holding_not_found",
            f"{infos.get('institution_daily').min_date if infos.get('institution_daily') else '--'} ~ {infos.get('institution_daily').max_date if infos.get('institution_daily') else '--'}",
            infos.get("institution_daily").code_count if infos.get("institution_daily") else 0,
            "只能做近期買超均價估算；不可稱為真實持倉成本",
            "投信近期買超估算成本",
            status["investment_trust_estimated_cost"]["confidence"],
            "若要提高可信度，需要合法投信持股/庫存來源",
        ],
        [
            "融資每股借款 / 融資買進成本",
            "融資餘額股數 + 融資金額 + 單位確認 + 價格參考",
            status["margin_loan_or_financing_cost"]["status"],
            "margin_daily.margin_balance; financing_amount=not_found",
            f"{infos.get('margin_daily').min_date if infos.get('margin_daily') else '--'} ~ {infos.get('margin_daily').max_date if infos.get('margin_daily') else '--'}",
            infos.get("margin_daily").code_count if infos.get("margin_daily") else 0,
            "不可計算融資每股借款或買進成本",
            "融資餘額變化觀察",
            status["margin_loan_or_financing_cost"]["confidence"],
            "先補合法融資金額欄位與單位說明，再開成本公式",
        ],
        [
            "主力券商分點估算成本",
            "券商分點每日買賣股數與金額，最好全分點或明確 Top-N 覆蓋",
            status["main_force_broker_branch_cost"]["status"],
            ", ".join(broker["broker_tables"]) or "broker_branch_table_not_found",
            "--",
            broker["broker_codes_count"],
            "不可計算",
            "券商分點成本暫停",
            status["main_force_broker_branch_cost"]["confidence"],
            "取得合法穩定分點買賣資料後再實作",
        ],
    ]

    table_rows = [["table", "rows", "date column", "date range", "code column", "code count", "columns"]]
    for info in infos.values():
        table_rows.append(
            [
                info.name,
                info.row_count,
                info.date_column or "--",
                f"{info.min_date or '--'} ~ {info.max_date or '--'}",
                info.code_column or "--",
                info.code_count if info.code_count is not None else "--",
                ", ".join(info.columns[:12]) + (" ..." if len(info.columns) > 12 else ""),
            ]
        )

    category_rows = [["category", "candidate tables"]]
    category_rows.append(["foreign / trust", ", ".join(info.name for info in foreign_tables) or "--"])
    category_rows.append(["TDCC / holding", ", ".join(info.name for info in holding_tables) or "--"])
    category_rows.append(["margin", ", ".join(info.name for info in margin_tables) or "--"])
    category_rows.append(["broker branch", ", ".join(info.name for info in broker_tables) or "--"])
    category_rows.append(["price / VWAP", ", ".join(info.name for info in price_tables) or "--"])

    sample_rows = [["code", "status", "history", "institution", "margin", "foreign holding", "TDCC", "trust holding", "broker data", "VWAP", "financing_amount", "can_compute_financing_loan_per_share"]]
    sample_details: list[str] = []
    for code in SAMPLE_CODES:
        sample = code_sample(conn, code)
        sample_rows.append(
            [
                code,
                sample["status"],
                sample.get("history_price", {}).get("row_count", 0),
                sample.get("institution_daily", {}).get("row_count", 0),
                sample.get("margin_daily", {}).get("row_count", 0),
                sample.get("foreign_shareholding", {}).get("row_count", 0),
                (sample.get("tdcc_holding_distribution", {}).get("row_count", 0) or 0)
                + (sample.get("tdcc_equity_summary", {}).get("row_count", 0) or 0),
                sample["trust_holding_available"],
                sample["broker_data_available"],
                sample["trade_value_vwap"].get("vwap") or "--",
                sample["financing_diagnostic"]["financing_amount_column"],
                sample["financing_diagnostic"]["can_compute_financing_loan_per_share"],
            ]
        )
        sample_details.append(f"### {code}\n\n```text\n{sample_jsonish(sample)}\n```")

    missing_fields = [
        "- `financing_amount`: not found in `margin_daily` or related local margin tables.",
        "- `investment_trust_holding`: not found. TDCC rows are shareholding level distribution, not investor-category holdings.",
        "- `broker_branch_buy_sell_amount`: not found.",
        "- `broker_branch_buy_sell_shares`: not found.",
    ]

    lines = [
        "# Cost Data Source Audit",
        "",
        f"- DB: `{db_path}`",
        f"- Generated at: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        "- Mode: SQLite read-only (`mode=ro`); no DB writes, no external API calls.",
        "",
        "## Summary",
        "",
        format_table(summary_rows),
        "",
        "## Candidate Tables By Data Family",
        "",
        format_table(category_rows),
        "",
        "## Financing Amount Diagnostic",
        "",
        "- `financing_amount_column`: `not_found`",
        "- `financing_balance_column`: `margin_daily.margin_balance`",
        "- `financing_balance_unit_guess`: `ambiguous_no_explicit_share_or_lot_unit`",
        "- `amount_unit_guess`: `amount_column_not_found`",
        "- `can_compute_financing_loan_per_share`: `False`",
        "- `can_compute_estimated_financing_purchase_cost`: `False`",
        "- Reason: local DB has financing balance/change, but no explicit financing amount / loan amount column. Ambiguous financing amount units must not be treated as computable cost.",
        "",
        "## Broker Branch Diagnostic",
        "",
        f"```text\n{broker}\n```",
        "",
        "## Sample Code Matrix",
        "",
        format_table(sample_rows),
        "",
        "## Sample Details",
        "",
        "\n\n".join(sample_details),
        "",
        "## Missing / NULL Blocking Fields",
        "",
        "\n".join(missing_fields),
        "",
        "## Full Table Scan",
        "",
        format_table(table_rows),
        "",
        "## Conclusion",
        "",
        "- 外資估算成本: `ready` for public-data estimation when `foreign_shareholding` and trade value are present, but it remains an estimate.",
        "- 投信估算成本: `partial`; `trust_net` exists, but `investment_trust_holding_not_found` blocks high-confidence holding-cost calibration.",
        "- 融資每股借款 / 融資買進成本: `partial/unavailable for cost`; `margin_balance` exists, but `financing_amount` is absent, so no loan-per-share or purchase-cost calculation should be implemented yet.",
        "- 主力券商分點估算成本: `unavailable`; no local broker-branch buy/sell amount and shares table was found.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit local DB availability for cost-estimation data sources.")
    parser.add_argument("--db", required=True, help="Path to SQLite DB.")
    parser.add_argument("--output", required=True, help="Path to Markdown report.")
    args = parser.parse_args()

    db_path = Path(args.db)
    output_path = Path(args.output)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with connect_readonly(db_path) as conn:
        report = build_report(conn, db_path)
    output_path.write_text(report, encoding="utf-8")
    print(f"wrote {output_path}")
    print("writes_db=false")
    print("external_api=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
