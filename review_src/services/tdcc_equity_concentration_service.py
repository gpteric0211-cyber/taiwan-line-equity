from __future__ import annotations

import csv
import io
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from typing import Any

import requests

from core.config import FINMIND_API, FINMIND_TOKEN, HEADERS, TDCC_HOLDING_DISTRIBUTION_CSV_URL, safe_error

try:
    import truststore  # type: ignore
except Exception:
    truststore = None
else:
    try:
        truststore.inject_into_ssl()
    except Exception:
        pass


TDCC_SOURCE = "TDCC_OPEN_DATA_1_5"
FINMIND_SOURCE = "FinMind_TaiwanStockHoldingSharesPer"

LEVEL_BOUNDS: dict[int, tuple[int, int | None]] = {
    1: (1, 999),
    2: (1000, 5000),
    3: (5001, 10000),
    4: (10001, 15000),
    5: (15001, 20000),
    6: (20001, 30000),
    7: (30001, 40000),
    8: (40001, 50000),
    9: (50001, 100000),
    10: (100001, 200000),
    11: (200001, 400000),
    12: (400001, 600000),
    13: (600001, 800000),
    14: (800001, 1000000),
    15: (1000001, None),
}

TDCC_CANONICAL_KEYS: dict[str, str] = {
    "資料日期": "資料日期",
    "date": "資料日期",
    "證券代號": "證券代號",
    "stock_id": "證券代號",
    "code": "證券代號",
    "持股分級": "持股分級",
    "HoldingSharesLevel": "持股分級",
    "level": "持股分級",
    "人數": "人數",
    "people": "人數",
    "holders": "人數",
    "股數": "股數",
    "shares": "股數",
    "占集保庫存數比例%": "占集保庫存數比例%",
    "占集保庫存數比例％": "占集保庫存數比例%",
    "percent": "占集保庫存數比例%",
    "percentage": "占集保庫存數比例%",
}


def _to_float(value: Any) -> float | None:
    s = str(value or "").strip().replace(",", "").replace("%", "").replace("％", "")
    if not s or s in {"--", "-", "nan", "None"}:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _to_int(value: Any) -> int | None:
    n = _to_float(value)
    return int(n) if n is not None else None


def normalize_tdcc_csv_key(key: Any) -> str:
    """Normalize TDCC CSV headers while preserving canonical field names."""
    text = str(key or "").replace("\ufeff", "").replace("\u3000", " ").strip()
    text = re.sub(r"\s+", "", text).replace("％", "%")
    return TDCC_CANONICAL_KEYS.get(text, text)


def normalize_tdcc_code(value: Any) -> str | None:
    """Return a clean 4-digit stock code, or None for non-equity codes."""
    if value is None:
        return None
    raw = str(value).replace("\ufeff", "").strip()
    if not raw:
        return None
    if re.search(r"[A-Za-z]", raw):
        return None
    if re.fullmatch(r"\d+\.0+", raw):
        raw = raw.split(".", 1)[0]
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if len(digits) < 4:
        digits = digits.zfill(4)
    elif len(digits) > 4:
        if digits[:-4].strip("0"):
            return None
        digits = digits[-4:]
    if re.fullmatch(r"\d{4}", digits):
        return digits
    return None


def normalize_tdcc_date(value: Any) -> str | None:
    s = str(value or "").replace("\ufeff", "").strip()
    if not s:
        return None
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{2,3})[-/年](\d{1,2})[-/月](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)) + 1911:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    digits = re.sub(r"\D", "", s)
    if len(digits) == 8:
        return f"{int(digits[:4]):04d}-{int(digits[4:6]):02d}-{int(digits[6:8]):02d}"
    if len(digits) == 7:
        return f"{int(digits[:3]) + 1911:04d}-{int(digits[3:5]):02d}-{int(digits[5:7]):02d}"
    return None


def _normalize_code(value: Any) -> str | None:
    return normalize_tdcc_code(value)


def _normalize_date(value: Any) -> str | None:
    return normalize_tdcc_date(value)


def _normalize_tdcc_row_keys(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        out[normalize_tdcc_csv_key(key)] = value
    return out


def parse_holding_level_bounds(level: Any) -> tuple[int | None, int | None, int | None]:
    text = str(level or "").strip()
    if not text or any(x in text for x in ["合計", "總計"]):
        return None, None, None
    if re.fullmatch(r"\d{1,2}", text):
        level_no = int(text)
        if level_no in LEVEL_BOUNDS:
            low, high = LEVEL_BOUNDS[level_no]
            return level_no, low, high
    match = re.match(r"^\s*(\d{1,2})\D", text)
    level_no = int(match.group(1)) if match else None
    if level_no in LEVEL_BOUNDS:
        low, high = LEVEL_BOUNDS[level_no]
        return level_no, low, high
    nums = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", text)]
    if len(nums) >= 2:
        return level_no, nums[0], nums[1]
    if len(nums) == 1 and re.search(r"以上|over|up", text, re.IGNORECASE):
        return level_no, nums[0], None
    return level_no, None, None


def parse_tdcc_holding_row(row: dict[str, Any]) -> dict[str, Any] | None:
    row = _normalize_tdcc_row_keys(row)
    date = normalize_tdcc_date(row.get("資料日期"))
    code = normalize_tdcc_code(row.get("證券代號"))
    level = str(row.get("持股分級") or "").strip()
    if not date or not code or not level or any(x in level for x in ["合計", "總計"]):
        return None
    level_no, low, high = parse_holding_level_bounds(level)
    if low is None:
        return None
    return {
        "date": date,
        "code": code,
        "level": str(level_no or level),
        "level_raw": level,
        "level_no": level_no,
        "lower_shares": low,
        "upper_shares": high,
        "holders": _to_int(row.get("人數")),
        "shares": _to_float(row.get("股數")),
        "percent": _to_float(row.get("占集保庫存數比例%")),
        "source": row.get("source") or TDCC_SOURCE,
    }


def _decode_csv_content_with_encoding(content: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace"), "utf-8-replace"


def _decode_csv_content(content: bytes) -> str:
    return _decode_csv_content_with_encoding(content)[0]


def fetch_tdcc_holding_distribution_csv_debug(url: str | None = None, timeout: int = 30) -> dict[str, Any]:
    actual_url = url or TDCC_HOLDING_DISTRIBUTION_CSV_URL
    resp = requests.get(actual_url, headers=HEADERS, timeout=timeout)
    text, encoding = _decode_csv_content_with_encoding(resp.content)
    resp.raise_for_status()
    return {
        "ok": True,
        "url": actual_url,
        "http_status": resp.status_code,
        "encoding": encoding,
        "text": text,
    }


def fetch_tdcc_holding_distribution_csv(url: str | None = None, timeout: int = 30) -> str:
    return str(fetch_tdcc_holding_distribution_csv_debug(url=url, timeout=timeout).get("text") or "")


def parse_tdcc_open_data_csv_debug(csv_text: str, target_code: str | None = None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    raw_headers = list(reader.fieldnames or [])
    normalized_headers = [normalize_tdcc_csv_key(x) for x in raw_headers]
    target = normalize_tdcc_code(target_code) if target_code else None
    first_raw_rows: list[dict[str, Any]] = []
    raw_code_contains_target_count = 0
    total_raw_rows = 0
    parsed_row_count = 0
    for raw in reader:
        total_raw_rows += 1
        if len(first_raw_rows) < 5:
            first_raw_rows.append(dict(raw))
        normalized_raw = _normalize_tdcc_row_keys(raw)
        raw_code_text = str(normalized_raw.get("證券代號") or "")
        if target and target in raw_code_text:
            raw_code_contains_target_count += 1
        parsed = parse_tdcc_holding_row({**raw, "source": TDCC_SOURCE})
        if parsed:
            rows.append(parsed)
            parsed_row_count += 1
    target_rows = [r for r in rows if target and r.get("code") == target]
    return {
        "rows": rows,
        "debug": {
            "csv_header_raw": raw_headers,
            "csv_header_normalized": normalized_headers,
            "total_raw_rows": total_raw_rows,
            "parsed_row_count": parsed_row_count,
            "first_5_raw_rows": first_raw_rows,
            "target_code": target,
            "raw_code_contains_target": bool(raw_code_contains_target_count),
            "raw_code_contains_target_count": raw_code_contains_target_count,
            "filter_before_count": parsed_row_count,
            "filter_after_count": len(target_rows) if target else None,
        },
    }


def parse_tdcc_open_data_csv(csv_text: str) -> list[dict[str, Any]]:
    return list(parse_tdcc_open_data_csv_debug(csv_text).get("rows") or [])


def fetch_finmind_holding_shares_per(code: str, days: int = 180, timeout: int = 30) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "dataset": "TaiwanStockHoldingSharesPer",
        "data_id": str(code).zfill(4),
    }
    if FINMIND_TOKEN:
        params["token"] = FINMIND_TOKEN
    resp = requests.get(FINMIND_API, params=params, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") or []
    cutoff: str | None = None
    if days > 0:
        try:
            latest = max(str(x.get("date")) for x in data if x.get("date"))
            latest_dt = datetime.strptime(latest, "%Y-%m-%d")
            cutoff = (latest_dt.timestamp() - days * 86400)
        except Exception:
            cutoff = None
    rows: list[dict[str, Any]] = []
    for item in data:
        d = _normalize_date(item.get("date"))
        if cutoff is not None and d:
            try:
                if datetime.strptime(d, "%Y-%m-%d").timestamp() < float(cutoff):
                    continue
            except Exception:
                pass
        level_raw = item.get("HoldingSharesLevel")
        level_no, low, high = parse_holding_level_bounds(level_raw)
        if low is None:
            continue
        rows.append({
            "date": d,
            "code": _normalize_code(item.get("stock_id")) or str(code).zfill(4),
            "level": str(level_no or level_raw),
            "level_raw": str(level_raw or ""),
            "level_no": level_no,
            "lower_shares": low,
            "upper_shares": high,
            "holders": _to_int(item.get("people")),
            "shares": None,
            "percent": _to_float(item.get("percent")),
            "source": FINMIND_SOURCE,
        })
    return [x for x in rows if x.get("date") and x.get("code")]


def _label_for_score(score: float | None) -> str:
    if score is None:
        return "股權集中度資料不足"
    if score >= 70:
        return "股權集中偏高"
    if score >= 55:
        return "股權略集中"
    if score >= 45:
        return "股權結構中性"
    if score >= 30:
        return "股權偏分散"
    return "股權分散風險高"


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def compute_equity_concentration_from_distribution(
    rows: list[dict[str, Any]],
    previous_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    valid = [r for r in rows if r.get("lower_shares") is not None]
    code = str(valid[0].get("code") if valid else rows[0].get("code") if rows else "")
    date = str(valid[0].get("date") if valid else rows[0].get("date") if rows else "")
    source = str(valid[0].get("source") if valid else rows[0].get("source") if rows else "")
    if len(valid) < 10:
        return {
            "date": date,
            "code": code,
            "quality": "parse_error",
            "equity_label": "股權集中度資料不足",
            "equity_reason": "持股分級有效資料不足，暫不推估集中度。",
            "source": source,
        }
    total_holders = sum(int(r.get("holders") or 0) for r in valid)
    total_shares = sum(float(r.get("shares") or 0) for r in valid) or None

    def pct_for(predicate) -> float | None:
        source_pct = [r.get("percent") for r in valid if predicate(r) and r.get("percent") is not None]
        if source_pct:
            return round(sum(float(x) for x in source_pct), 4)
        if total_shares:
            shares = sum(float(r.get("shares") or 0) for r in valid if predicate(r))
            return round(shares / total_shares * 100.0, 4)
        return None

    # The open-ended highest band has ``upper_shares=None``.  Treating that
    # value as zero incorrectly classified every 1,000-lot-plus holder as a
    # small holder and materially inflated ``small_10_share_pct``.
    small_10 = pct_for(
        lambda r: r.get("upper_shares") is not None
        and int(r["upper_shares"]) <= 10000
    )
    big_400 = pct_for(lambda r: (r.get("lower_shares") or 0) >= 400000)
    big_1000 = pct_for(lambda r: (r.get("lower_shares") or 0) >= 1000000)

    def change(field: str, current: float | int | None) -> float | None:
        if previous_summary is None or current is None or previous_summary.get(field) is None:
            return None
        return round(float(current) - float(previous_summary[field]), 4)

    holder_change = change("total_holders", total_holders)
    holder_change_pct = None
    if previous_summary and previous_summary.get("total_holders") not in (None, 0) and holder_change is not None:
        holder_change_pct = round(holder_change / float(previous_summary["total_holders"]) * 100.0, 4)

    score = 50.0
    if big_400 is not None:
        score += (float(big_400) - 50.0) * 0.5
    big_400_change = change("big_400_share_pct", big_400)
    small_10_change = change("small_10_share_pct", small_10)
    big_1000_change = change("big_1000_share_pct", big_1000)
    if big_400_change is not None:
        score += big_400_change * 2.0
    if small_10_change is not None:
        score -= small_10_change * 1.5
    if holder_change_pct is not None and holder_change_pct > 2:
        score -= min(10.0, holder_change_pct)
    score = round(_clamp(score), 2)
    reason_parts = []
    if big_400 is not None:
        reason_parts.append(f"400張以上持股占比 {big_400:.2f}%")
    if big_400_change is not None:
        reason_parts.append(f"近4週變化 {big_400_change:+.2f}百分點")
    else:
        reason_parts.append("歷史週資料不足，近4週變化暫無法計算")
    if small_10 is not None:
        reason_parts.append(f"小戶持股占比 {small_10:.2f}%")
    return {
        "date": date,
        "code": code,
        "total_holders": total_holders,
        "total_shares": total_shares,
        "small_10_share_pct": small_10,
        "big_400_share_pct": big_400,
        "big_1000_share_pct": big_1000,
        "small_10_change_4w": small_10_change,
        "big_400_change_4w": big_400_change,
        "big_1000_change_4w": big_1000_change,
        "holder_count_change_4w": holder_change,
        "holder_count_change_4w_pct": holder_change_pct,
        "equity_score": score,
        "equity_label": _label_for_score(score),
        "equity_reason": "；".join(reason_parts),
        "source": source,
        "quality": "ok",
    }


def upsert_tdcc_holding_distribution(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    now = time.time()
    count = 0
    for row in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO tdcc_holding_distribution
            (date, code, level, holders, shares, percent, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("date"),
                row.get("code"),
                str(row.get("level")),
                row.get("holders"),
                row.get("shares"),
                row.get("percent"),
                row.get("source"),
                now,
            ),
        )
        count += 1
    return count


def _rows_for_code_date(conn: sqlite3.Connection, code: str, d: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT date, code, level, holders, shares, percent, source
        FROM tdcc_holding_distribution
        WHERE code=? AND date=?
        ORDER BY CAST(level AS INTEGER)
        """,
        (str(code).zfill(4), d),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        level_no, low, high = parse_holding_level_bounds(row["level"])
        out.append({**dict(row), "level_no": level_no, "lower_shares": low, "upper_shares": high})
    return out


def _latest_dates(conn: sqlite3.Connection, code: str, limit: int = 8) -> list[str]:
    return [
        str(r["date"])
        for r in conn.execute(
            "SELECT DISTINCT date FROM tdcc_holding_distribution WHERE code=? ORDER BY date DESC LIMIT ?",
            (str(code).zfill(4), int(limit)),
        ).fetchall()
    ]


def _summary_for_date(conn: sqlite3.Connection, code: str, d: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM tdcc_equity_summary WHERE code=? AND date=?", (str(code).zfill(4), d)).fetchone()
    return dict(row) if row else None


def compute_tdcc_equity_summary_for_code(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    code = str(code).zfill(4)
    dates = _latest_dates(conn, code, 8)
    if not dates:
        return {
            "code": code,
            "quality": "missing",
            "equity_label": "股權集中度資料不足",
            "equity_reason": "尚未匯入 TDCC 集保股權分散週資料。",
        }
    current_date = dates[0]
    previous = None
    if len(dates) >= 5:
        previous_rows = _rows_for_code_date(conn, code, dates[4])
        previous = compute_equity_concentration_from_distribution(previous_rows) if previous_rows else None
    rows = _rows_for_code_date(conn, code, current_date)
    return compute_equity_concentration_from_distribution(rows, previous)


def upsert_tdcc_equity_summary_for_codes(conn: sqlite3.Connection, codes: list[str]) -> int:
    now = time.time()
    written = 0
    for code in sorted({str(x).zfill(4) for x in codes if str(x).strip()}):
        summary = compute_tdcc_equity_summary_for_code(conn, code)
        if not summary.get("date"):
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO tdcc_equity_summary
            (date, code, total_holders, total_shares, small_10_share_pct, big_400_share_pct,
             big_1000_share_pct, small_10_change_4w, big_400_change_4w, big_1000_change_4w,
             holder_count_change_4w, holder_count_change_4w_pct, equity_score, equity_label,
             equity_reason, source, quality, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary.get("date"),
                code,
                summary.get("total_holders"),
                summary.get("total_shares"),
                summary.get("small_10_share_pct"),
                summary.get("big_400_share_pct"),
                summary.get("big_1000_share_pct"),
                summary.get("small_10_change_4w"),
                summary.get("big_400_change_4w"),
                summary.get("big_1000_change_4w"),
                summary.get("holder_count_change_4w"),
                summary.get("holder_count_change_4w_pct"),
                summary.get("equity_score"),
                summary.get("equity_label"),
                summary.get("equity_reason"),
                summary.get("source"),
                summary.get("quality"),
                now,
            ),
        )
        written += 1
    return written


def rebuild_tdcc_equity_summaries_from_persisted_distribution(
    conn: sqlite3.Connection,
    codes: list[str] | None = None,
) -> dict[str, Any]:
    """Rebuild every persisted weekly summary without fetching external data.

    The four-week comparison is the fourth prior weekly observation for the
    same stock.  Rebuilding every date is required after a formula correction;
    updating only the latest row would leave historical outcomes inconsistent.
    """

    selected_codes = _clean_code_list(codes or [])
    if not selected_codes:
        selected_codes = [
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT code FROM tdcc_holding_distribution ORDER BY code"
            ).fetchall()
        ]
    before = conn.total_changes
    summary_count = 0
    for code in selected_codes:
        dates = [
            str(row[0])
            for row in conn.execute(
                """
                SELECT DISTINCT date FROM tdcc_holding_distribution
                WHERE code=? ORDER BY date
                """,
                (code,),
            ).fetchall()
        ]
        for index, current_date in enumerate(dates):
            previous = None
            if index >= 4:
                previous_rows = _rows_for_code_date(conn, code, dates[index - 4])
                if previous_rows:
                    previous = compute_equity_concentration_from_distribution(
                        previous_rows
                    )
            current_rows = _rows_for_code_date(conn, code, current_date)
            summary = compute_equity_concentration_from_distribution(
                current_rows,
                previous,
            )
            if not summary.get("date"):
                continue
            now = time.time()
            conn.execute(
                """
                INSERT INTO tdcc_equity_summary(
                    date,code,total_holders,total_shares,small_10_share_pct,
                    big_400_share_pct,big_1000_share_pct,small_10_change_4w,
                    big_400_change_4w,big_1000_change_4w,
                    holder_count_change_4w,holder_count_change_4w_pct,
                    equity_score,equity_label,equity_reason,source,quality,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(date,code) DO UPDATE SET
                    total_holders=excluded.total_holders,
                    total_shares=excluded.total_shares,
                    small_10_share_pct=excluded.small_10_share_pct,
                    big_400_share_pct=excluded.big_400_share_pct,
                    big_1000_share_pct=excluded.big_1000_share_pct,
                    small_10_change_4w=excluded.small_10_change_4w,
                    big_400_change_4w=excluded.big_400_change_4w,
                    big_1000_change_4w=excluded.big_1000_change_4w,
                    holder_count_change_4w=excluded.holder_count_change_4w,
                    holder_count_change_4w_pct=excluded.holder_count_change_4w_pct,
                    equity_score=excluded.equity_score,
                    equity_label=excluded.equity_label,
                    equity_reason=excluded.equity_reason,
                    source=excluded.source,
                    quality=excluded.quality,
                    updated_at=excluded.updated_at
                """,
                (
                    summary.get("date"),
                    code,
                    summary.get("total_holders"),
                    summary.get("total_shares"),
                    summary.get("small_10_share_pct"),
                    summary.get("big_400_share_pct"),
                    summary.get("big_1000_share_pct"),
                    summary.get("small_10_change_4w"),
                    summary.get("big_400_change_4w"),
                    summary.get("big_1000_change_4w"),
                    summary.get("holder_count_change_4w"),
                    summary.get("holder_count_change_4w_pct"),
                    summary.get("equity_score"),
                    summary.get("equity_label"),
                    summary.get("equity_reason"),
                    summary.get("source"),
                    summary.get("quality"),
                    now,
                ),
            )
            summary_count += 1
    return {
        "code_count": len(selected_codes),
        "summary_count": summary_count,
        "rows_changed": conn.total_changes - before,
        "source_fetch_count": 0,
    }


def import_equity_rows_and_summarize(conn: sqlite3.Connection, rows: list[dict[str, Any]], codes: list[str]) -> dict[str, Any]:
    wanted = {str(x).zfill(4) for x in codes}
    filtered = [r for r in rows if r.get("code") in wanted]
    inserted = upsert_tdcc_holding_distribution(conn, filtered)
    summaries = upsert_tdcc_equity_summary_for_codes(conn, list(wanted))
    return {"rows": inserted, "summaries": summaries, "codes": len(wanted)}


def _clean_code_list(codes: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in codes:
        code = normalize_tdcc_code(value)
        if code and code not in seen:
            cleaned.append(code)
            seen.add(code)
    return cleaned


def _latest_row_date(rows: list[dict[str, Any]]) -> str | None:
    dates = [str(row.get("date")) for row in rows if row.get("date")]
    return max(dates) if dates else None


def _empty_update_result(
    codes: list[str],
    *,
    source: str,
    dry_run: bool,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "source": source,
        "dry_run": bool(dry_run),
        "total_codes": len(codes),
        "success": 0,
        "missing": len(codes),
        "parse_error": 0,
        "stale": 0,
        "distribution_rows": 0,
        "summaries": 0,
        "latest_date": None,
        "errors": errors or [],
        "codes": codes,
    }


def _summary_quality_counts(conn: sqlite3.Connection, codes: list[str]) -> dict[str, int]:
    counts = {"success": 0, "missing": 0, "parse_error": 0, "stale": 0}
    for code in codes:
        payload = latest_equity_concentration_payload(code, conn=conn)
        quality = str(payload.get("quality") or "missing")
        if quality == "ok":
            counts["success"] += 1
        elif quality == "stale":
            counts["stale"] += 1
        elif quality == "parse_error":
            counts["parse_error"] += 1
        else:
            counts["missing"] += 1
    return counts


def _load_tdcc_rows_for_codes(codes: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    fetched = fetch_tdcc_holding_distribution_csv_debug()
    parsed = parse_tdcc_open_data_csv_debug(str(fetched.get("text") or ""))
    wanted = set(codes)
    rows = [row for row in (parsed.get("rows") or []) if row.get("code") in wanted]
    debug = {
        "download_url": fetched.get("url"),
        "http_status": fetched.get("http_status"),
        "encoding": fetched.get("encoding"),
        **dict(parsed.get("debug") or {}),
    }
    errors: list[dict[str, Any]] = []
    for code in codes:
        if not any(row.get("code") == code for row in rows):
            errors.append({"code": code, "stage": "tdcc", "error": "TDCC rows not found for code"})
    return rows, debug, errors


def _load_finmind_rows_for_codes(codes: list[str], days: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for code in codes:
        try:
            code_rows = fetch_finmind_holding_shares_per(code, days=days)
            if code_rows:
                rows.extend(code_rows)
            else:
                errors.append({"code": code, "stage": "finmind", "error": "FinMind returned no usable rows"})
        except Exception as exc:
            errors.append({"code": code, "stage": "finmind", "error": safe_error(exc)})
    return rows, errors


def update_tdcc_equity_concentration_for_codes(
    codes: list[str],
    *,
    source: str = "tdcc",
    days: int = 180,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """
    Update TDCC equity concentration for an explicit code list.

    This helper is intended for manual scripts and explicit POST update flows.
    It does not update app status, prune caches, create background tasks, or run
    from read-only GET paths.
    """
    source = (source or "tdcc").strip().lower()
    if source not in {"tdcc", "finmind", "auto"}:
        return _empty_update_result([], source=source, dry_run=dry_run, errors=[{
            "code": None,
            "stage": "input",
            "error": "source must be tdcc, finmind, or auto",
        }])
    clean_codes = _clean_code_list(codes)
    if limit is not None and int(limit) >= 0:
        clean_codes = clean_codes[: int(limit)]
    if not clean_codes:
        return _empty_update_result([], source=source, dry_run=dry_run, errors=[{
            "code": None,
            "stage": "input",
            "error": "no valid 4-digit stock codes",
        }])

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    source_debug: dict[str, Any] = {}

    if source in {"tdcc", "auto"}:
        try:
            tdcc_rows, tdcc_debug, tdcc_errors = _load_tdcc_rows_for_codes(clean_codes)
            rows.extend(tdcc_rows)
            source_debug["tdcc"] = tdcc_debug
            if source == "tdcc":
                errors.extend(tdcc_errors)
        except Exception as exc:
            errors.append({"code": None, "stage": "tdcc", "error": safe_error(exc)})

    if source in {"finmind", "auto"}:
        found_codes = {str(row.get("code")) for row in rows if row.get("code")}
        missing_codes = [code for code in clean_codes if code not in found_codes]
        if source == "finmind":
            missing_codes = clean_codes
            rows = []
        if missing_codes:
            finmind_rows, finmind_errors = _load_finmind_rows_for_codes(missing_codes, days)
            rows.extend(finmind_rows)
            errors.extend(finmind_errors)

    rows = [row for row in rows if row.get("code") in set(clean_codes)]
    latest_date = _latest_row_date(rows)
    rows_by_code: dict[str, list[dict[str, Any]]] = {code: [] for code in clean_codes}
    for row in rows:
        rows_by_code.setdefault(str(row.get("code")), []).append(row)

    if dry_run:
        success = 0
        parse_error = 0
        missing = 0
        for code in clean_codes:
            code_rows = rows_by_code.get(code) or []
            if not code_rows:
                missing += 1
                continue
            summary = compute_equity_concentration_from_distribution(code_rows)
            quality = str(summary.get("quality") or "missing")
            if quality == "ok":
                success += 1
            elif quality == "parse_error":
                parse_error += 1
            else:
                missing += 1
        return {
            "ok": success > 0 and parse_error == 0,
            "source": source,
            "dry_run": True,
            "total_codes": len(clean_codes),
            "success": success,
            "missing": missing,
            "parse_error": parse_error,
            "stale": 0,
            "distribution_rows": len(rows),
            "summaries": success,
            "latest_date": latest_date,
            "errors": errors,
            "codes": clean_codes,
            "source_debug": source_debug,
        }

    from core.db import db

    with closing(db()) as conn:
        result = import_equity_rows_and_summarize(conn, rows, clean_codes)
        conn.commit()
        counts = _summary_quality_counts(conn, clean_codes)
    return {
        "ok": counts["success"] > 0 and counts["parse_error"] == 0,
        "source": source,
        "dry_run": False,
        "total_codes": len(clean_codes),
        **counts,
        "distribution_rows": int(result.get("rows") or 0),
        "summaries": int(result.get("summaries") or 0),
        "latest_date": latest_date,
        "errors": errors,
        "codes": clean_codes,
        "source_debug": source_debug,
    }


def latest_equity_concentration_payload(
    code: str,
    conn: sqlite3.Connection | None = None,
    *,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    own_conn = conn is None
    if own_conn:
        from core.db import db
        conn = db()
    try:
        if as_of_date:
            row = conn.execute(
                """
                SELECT * FROM tdcc_equity_summary
                WHERE code=? AND date<=?
                ORDER BY date DESC
                LIMIT 1
                """,
                (str(code).zfill(4), str(as_of_date)),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM tdcc_equity_summary WHERE code=? ORDER BY date DESC LIMIT 1",
                (str(code).zfill(4),),
            ).fetchone()
        if not row:
            return {
                "available": False,
                "quality": "missing",
                "label": "股權集中度資料不足",
                "reason": "尚未匯入 TDCC 集保股權分散週資料。",
                "note": "請使用 POST /api/update/tdcc-equity-concentration 手動更新。",
            }
        data = dict(row)
        quality = str(data.get("quality") or "ok")
        try:
            lag_days = (datetime.now() - datetime.strptime(str(data.get("date")), "%Y-%m-%d")).days
        except Exception:
            lag_days = None
        if quality == "ok" and lag_days is not None and lag_days > 14:
            quality = "stale"
        return {
            "available": quality in {"ok", "stale"},
            "quality": quality,
            "date": data.get("date"),
            "label": data.get("equity_label") or "股權集中度資料不足",
            "score": data.get("equity_score"),
            "source": data.get("source"),
            "reason": data.get("equity_reason"),
            "metrics": {
                "total_holders": data.get("total_holders"),
                "total_shares": data.get("total_shares"),
                "small_10_share_pct": data.get("small_10_share_pct"),
                "big_400_share_pct": data.get("big_400_share_pct"),
                "big_1000_share_pct": data.get("big_1000_share_pct"),
            },
            "changes": {
                "small_10_change_4w": data.get("small_10_change_4w"),
                "big_400_change_4w": data.get("big_400_change_4w"),
                "big_1000_change_4w": data.get("big_1000_change_4w"),
                "holder_count_change_4w": data.get("holder_count_change_4w"),
                "holder_count_change_4w_pct": data.get("holder_count_change_4w_pct"),
            },
            "note": "週資料；反映中期持股結構，不代表短線分點買賣流向。",
        }
    except Exception as exc:
        return {
            "available": False,
            "quality": "parse_error",
            "label": "股權集中度資料不足",
            "reason": "讀取股權集中度資料失敗：" + safe_error(exc),
        }
    finally:
        if own_conn and conn is not None:
            conn.close()


def load_equity_rows_from_source(code: str, days: int = 180, source: str = "auto") -> dict[str, Any]:
    source = (source or "auto").lower()
    code = normalize_tdcc_code(code) or str(code).zfill(4)[:4]
    errors: list[str] = []
    debug: dict[str, Any] = {}
    if source in {"auto", "tdcc"}:
        try:
            fetched = fetch_tdcc_holding_distribution_csv_debug()
            parsed = parse_tdcc_open_data_csv_debug(str(fetched.get("text") or ""), target_code=code)
            debug["tdcc"] = {
                "download_url": fetched.get("url"),
                "http_status": fetched.get("http_status"),
                "encoding": fetched.get("encoding"),
                **dict(parsed.get("debug") or {}),
            }
            rows = [r for r in (parsed.get("rows") or []) if r.get("code") == code]
            if rows:
                return {"ok": True, "source": TDCC_SOURCE, "rows": rows, "row_count": len(rows), "debug": debug}
            errors.append("TDCC CSV parsed, but target code has no rows")
        except Exception as exc:
            errors.append("TDCC failed: " + safe_error(exc))
    if source in {"auto", "finmind"}:
        try:
            rows = fetch_finmind_holding_shares_per(code, days=days)
            if rows:
                return {"ok": True, "source": FINMIND_SOURCE, "rows": rows, "row_count": len(rows), "debug": debug}
            errors.append("FinMind returned no usable rows")
        except Exception as exc:
            errors.append("FinMind failed: " + safe_error(exc))
    return {"ok": False, "source": source, "rows": [], "row_count": 0, "errors": errors, "debug": debug}
