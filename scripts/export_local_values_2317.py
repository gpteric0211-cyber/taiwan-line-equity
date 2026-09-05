from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
OUT_PATH = ROOT / "docs" / "local_values_2317.md"
STOCK_CODE = "2317"
VENV_PYTHON = REVIEW_SRC / ".venv" / "Scripts" / "python.exe"

if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    import subprocess

    raise SystemExit(subprocess.run([str(VENV_PYTHON), str(Path(__file__).resolve())]).returncode)

if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.config import DB_PATH  # noqa: E402
from core.data_quality import DataQualityStatus  # noqa: E402
from scoring import calculate_indicators  # noqa: E402


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    try:
        number = float(value)
    except Exception:
        return str(value)
    if number != number:
        return "N/A"
    if abs(number - round(number)) < 1e-9:
        return f"{int(round(number)):,}"
    return f"{number:,.{digits}f}".rstrip("0").rstrip(".")


def _last_value(ind, column: str, digits: int = 2) -> str:
    if column not in ind.columns or ind.empty:
        return "N/A"
    value = ind[column].iloc[-1]
    return _fmt(value, digits)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except Exception:
        return None
    if number != number:
        return None
    return number


def _source_flags(price_source: str, *, uses_mis_intraday: bool) -> dict[str, Any]:
    text = str(price_source or "")
    return {
        "uses_twse_official_close": "TWSE" in text or "STOCK_DAY" in text,
        "uses_finmind_fallback": "FinMind" in text,
        "uses_mis_intraday": bool(uses_mis_intraday),
    }


def _source_metadata(latest: dict[str, Any], prev: dict[str, Any] | None, eod: dict[str, Any] | None) -> list[tuple[str, str]]:
    price_source = latest.get("source") or (eod or {}).get("source") or "sqlite_history"
    data_trade_date = latest.get("date") or (eod or {}).get("date") or "N/A"
    previous_close = (prev or {}).get("close")
    previous_close_source = "history_price.close previous row" if prev else "unknown"
    watchlist_previous_close = _parse_float(previous_close)
    latest_close = _parse_float(latest.get("close"))
    watchlist_mis_usable = "unknown - no live MIS fetch in this export script"
    data_quality = DataQualityStatus.OK.value if latest_close is not None else DataQualityStatus.MISSING.value
    flags = _source_flags(price_source, uses_mis_intraday=False)
    return [
        ("source_type: watchlist_realtime", "Would use MIS only if live price is numeric, positive, and within 10% of previous close."),
        ("source_type: taiwan50_batch", "Uses local close-batch/history data and must not use MIS intraday."),
        ("source_type: detail_realtime", "Uses watchlist realtime boundary only when the stock is in watchlist."),
        ("source_type: detail_batch", "Uses close-batch/history boundary for non-watchlist detail."),
        ("price_source", str(price_source)),
        ("technical_source", "history_price"),
        ("data_quality", data_quality),
        ("data_trade_date", str(data_trade_date)),
        ("fetched_at", datetime.now().isoformat(timespec="seconds")),
        ("actual_ohlcv_source", str(price_source)),
        ("using_twse_official_close", str(flags["uses_twse_official_close"])),
        ("using_finmind_fallback", str(flags["uses_finmind_fallback"])),
        ("using_mis_intraday", str(flags["uses_mis_intraday"])),
        ("taiwan50_batch_uses_mis", "False"),
        ("watchlist_realtime_mis_sanity", str(watchlist_mis_usable)),
        ("previous_close", _fmt(previous_close)),
        ("previous_close_source", previous_close_source),
        ("latest_close", _fmt(latest.get("close"))),
        ("latest_vs_previous_close_pct", (
            f"{((latest_close - watchlist_previous_close) / watchlist_previous_close * 100):.2f}%"
            if latest_close is not None and watchlist_previous_close not in (None, 0)
            else "unknown"
        )),
    ]


def _load_rows() -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    with closing(_connect()) as conn:
        hist = conn.execute(
            """
            SELECT date, code, open, high, low, close, volume, amount, volume_unit, source, updated_at
            FROM history_price
            WHERE code=?
            ORDER BY date DESC
            LIMIT 260
            """,
            (STOCK_CODE,),
        ).fetchall()
        eod = conn.execute(
            """
            SELECT date, code, name, open, high, low, close, volume, amount, change_value, source, updated_at
            FROM eod_price
            WHERE code=?
            ORDER BY date DESC
            LIMIT 1
            """,
            (STOCK_CODE,),
        ).fetchone()
    return [_row_to_dict(row) for row in reversed(hist)], (_row_to_dict(eod) if eod else None)


def _indicator_table(ind, latest: dict[str, Any], prev: dict[str, Any] | None) -> list[tuple[str, str, str]]:
    close = latest.get("close")
    prev_close = prev.get("close") if prev else None
    change = None
    change_pct = None
    try:
        if close is not None and prev_close not in (None, 0):
            change = float(close) - float(prev_close)
            change_pct = change / float(prev_close) * 100
    except Exception:
        change = None
        change_pct = None
    return [
        ("open", _fmt(latest.get("open")), "history_price.open"),
        ("high", _fmt(latest.get("high")), "history_price.high"),
        ("low", _fmt(latest.get("low")), "history_price.low"),
        ("close", _fmt(close), "history_price.close"),
        ("volume", _fmt(latest.get("volume"), 0), f"history_price.volume ({latest.get('volume_unit') or 'unit unknown'})"),
        ("change", _fmt(change), "local close - previous close"),
        ("change_percent", (_fmt(change_pct) + "%") if change_pct is not None else "N/A", "local change / previous close"),
        ("MA5", _last_value(ind, "ma5"), "scoring.calculate_indicators ma5"),
        ("MA10", _last_value(ind, "ma10"), "scoring.calculate_indicators ma10"),
        ("MA20", _last_value(ind, "ma20"), "scoring.calculate_indicators ma20"),
        ("MA60", _last_value(ind, "ma60"), "scoring.calculate_indicators ma60"),
        ("RSI5", _last_value(ind, "rsi5"), "scoring.calculate_indicators rsi5"),
        ("RSI10", _last_value(ind, "rsi10"), "scoring.calculate_indicators rsi10"),
        ("RSI14", _last_value(ind, "rsi14"), "scoring.calculate_indicators rsi14"),
        ("KD_K", _last_value(ind, "k"), "scoring.calculate_indicators k"),
        ("KD_D", _last_value(ind, "d"), "scoring.calculate_indicators d"),
        ("MACD_DIF", _last_value(ind, "dif", 4), "scoring.calculate_indicators dif"),
        ("MACD_DEA", _last_value(ind, "macd_signal", 4), "scoring.calculate_indicators macd_signal"),
        ("MACD_histogram", _last_value(ind, "osc", 4), "scoring.calculate_indicators osc"),
        ("BIAS", "N/A", "not implemented in current system"),
        ("Bollinger_upper", _last_value(ind, "boll_upper"), "scoring.calculate_indicators boll_upper"),
        ("Bollinger_mid", _last_value(ind, "boll_mid"), "scoring.calculate_indicators boll_mid"),
        ("Bollinger_lower", _last_value(ind, "boll_lower"), "scoring.calculate_indicators boll_lower"),
        ("DMI_plus_DI", "N/A", "not implemented in current system"),
        ("DMI_minus_DI", "N/A", "not implemented in current system"),
        ("ADX", "N/A", "not implemented in current system"),
    ]


def main() -> int:
    rows, eod = _load_rows()
    if not rows:
        OUT_PATH.write_text(
            "# Local Values Export: 2317\n\nNo local history_price rows found for 2317.\n",
            encoding="utf-8",
        )
        print(f"Wrote {OUT_PATH}")
        return 0

    import pandas as pd

    df = pd.DataFrame(rows)[["date", "open", "high", "low", "close", "volume"]].copy()
    ind = calculate_indicators(df)
    latest = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None
    price_source = latest.get("source") or (eod or {}).get("source") or "sqlite_history"
    data_trade_date = latest.get("date") or (eod or {}).get("date") or "N/A"
    missing_fields = [name for name, value, _src in _indicator_table(ind, latest, prev) if value == "N/A"]
    source_metadata = _source_metadata(latest, prev, eod)
    fallback_fields = []
    if price_source not in {"TWSE OpenAPI", "TWSE STOCK_DAY"}:
        fallback_fields.extend(["open", "high", "low", "close", "volume"])
    if latest.get("volume_unit") not in {"shares", "股", None, ""}:
        fallback_fields.append("volume_unit")

    lines = [
        "# Local Values Export: 2317 鴻海",
        "",
        f"- Run time: {datetime.now().isoformat(timespec='seconds')}",
        f"- Stock code: {STOCK_CODE}",
        f"- Data trade date: {data_trade_date}",
        f"- Price source: {price_source}",
        f"- DB path: `{DB_PATH}`",
        "",
        "## Values",
        "",
        "| Field | Value | Local source / formula |",
        "| --- | ---: | --- |",
    ]
    for field, value, source in _indicator_table(ind, latest, prev):
        lines.append(f"| {field} | {value} | {source} |")
    lines.extend([
        "",
        "## Source Metadata",
        "",
        "| Metadata | Value |",
        "| --- | --- |",
    ])
    for field, value in source_metadata:
        lines.append(f"| {field} | {value} |")
    lines.extend([
        "",
        "## Missing Fields",
        "",
        ", ".join(missing_fields) if missing_fields else "None",
        "",
        "## Fallback Fields",
        "",
        ", ".join(sorted(set(fallback_fields))) if fallback_fields else "None detected by this export script",
        "",
        "## Usage In Current System",
        "",
        "| Field group | Enters main status | Enters support/resistance | Enters next-day outlook |",
        "| --- | --- | --- | --- |",
        "| OHLCV history | Yes, through `score_stock_cached()` and `classify_practical_status_cached()` inputs | Yes, through support/resistance helpers | Yes, through technical factor inputs |",
        "| MA / RSI / KD / MACD | Yes, via `scoring.calculate_indicators()` and practical status inputs | Indirectly, price/technical context only | Yes, technical filter uses local indicators |",
        "| Bollinger | Yes, inside `scoring.calculate_indicators()` scoring internals | No direct display path found in this export | Possible via scoring-derived targets only |",
        "| BIAS / DMI / ADX | No, currently N/A | No | No |",
        "",
        "## Notes",
        "",
        "- This script reads only local SQLite/project functions.",
        "- It does not fetch or scrape Goodinfo, Yahoo, or WantGoo.",
        "- It does not write to the database or JSON cache.",
    ])
    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
