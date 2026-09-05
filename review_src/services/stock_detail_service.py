from __future__ import annotations

import logging
import math
import sqlite3
from contextlib import closing
from typing import Any

import pandas as pd

from core.components import read_components, resolve_stock
from core.config import safe_error
from core.db import db
from core.utils import fmt
from repository.watchlist_repository import get_watchlist_item

try:
    from scoring import calculate_indicators
except Exception:
    calculate_indicators = None


_INDICATOR_BASE_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
_INDICATOR_ADJUSTED_COLUMNS = [
    "rsi_close",
    "technical_open",
    "technical_high",
    "technical_low",
    "technical_close",
]


def _indicator_input_frame(rows_asc: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows_asc)
    columns = _INDICATOR_BASE_COLUMNS + [
        column for column in _INDICATOR_ADJUSTED_COLUMNS if column in frame.columns
    ]
    return frame[columns].copy()


def _last_finite_from_df(df: pd.DataFrame, col: str) -> float | None:
    try:
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        return float(s.iloc[-1]) if not s.empty else None
    except Exception:
        return None


def technical_detail_from_rows(rows_asc: list[dict[str, Any]]) -> dict[str, Any]:
    # TODO(accurate-data-source, 2026-06-19):
    # Ensure displayed price/technical values come from a clear authoritative source or documented local formula.
    # Goodinfo/Yahoo/WantGoo are manual verification references only, not scraping targets or frontend comparison fields.
    if not rows_asc or calculate_indicators is None:
        return {"items": [
            {"name": "技術資料狀態", "value": "需先更新日K", "explain": "尚未取得足夠日線資料，因此 RSI、MACD、KD、均線、ATR 無法準確計算。", "risk": "請先執行更新盤後/補齊 20 日；系統不會用猜測值填補技術指標。"}
        ], "note": "歷史K或技術模組不足"}
    try:
        df = _indicator_input_frame(rows_asc)
        ind = calculate_indicators(df)
        last = ind.iloc[-1]
        prev = ind.iloc[-2] if len(ind) >= 2 else last
        def val(col: str, digits: int = 2):
            v = _last_finite_from_df(ind, col)
            return round(v, digits) if v is not None else None
        close = val("close")
        volume = val("volume", 0)
        vol_ma20 = val("vol_ma20", 0)
        volume_ratio_20 = None
        if volume is not None and vol_ma20 not in (None, 0):
            volume_ratio_20 = round(float(volume) / float(vol_ma20), 2)
        ma5 = val("ma5")
        ma10 = val("ma10")
        ma20 = val("ma20")
        ma60 = val("ma60")
        atr14 = val("atr14")
        atr_pct = round(atr14 / close * 100, 2) if close and atr14 else None
        dif = val("dif", 4); macd_signal = val("macd_signal", 4); osc = val("osc", 4)
        k = val("k"); d = val("d")
        rsi5 = val("rsi5"); rsi10 = val("rsi10"); rsi14 = val("rsi14")
        obv = val("obv", 0)
        previous_10d_low = val("previous_10d_low")
        previous_20d_high = val("previous_20d_high")
        previous_60d_high = val("previous_60d_high")
        items = []
        def add(name, value, explain, risk=""):
            items.append({"name": name, "value": value if value is not None else "無資料", "explain": explain, "risk": risk})
        add("收盤/現價", fmt(close), "目前技術判斷的基準價格。", "若低於關鍵均線或前低，代表短線轉弱風險提高。")
        add("RSI5 / RSI10 / RSI14", f"{fmt(rsi5)} / {fmt(rsi10)} / {fmt(rsi14)}", "RSI 衡量短中線強弱；RSI5 看極短線，RSI10 看短線趨勢，RSI14 是標準參考。", "RSI 很低不代表可買，仍需確認是否跌破支撐。")
        add("MACD DIF / Signal / OSC", f"{fmt(dif,4)} / {fmt(macd_signal,4)} / {fmt(osc,4)}", "DIF 高於 Signal 且 OSC 改善偏多；反之偏弱。", "DIF 下彎或 OSC 連續縮小，容易出現動能鈍化。")
        add("KD K / D", f"{fmt(k)} / {fmt(d)}", "K 高於 D 偏短線轉強；K、D 過高時容易震盪。", "KD 高檔死亡交叉通常代表短線拉回風險。")
        add("MA5 / MA10 / MA20 / MA60", f"{fmt(ma5)} / {fmt(ma10)} / {fmt(ma20)} / {fmt(ma60)}", "MA5/MA10 看短線，MA20 代表月線，MA60 代表季線。", "跌破 MA20 後若又跌破 10 日低點，應視為破位警訊。")
        add("ATR14 / ATR%", f"{fmt(atr14)} / {fmt(atr_pct)}%", "ATR 代表近 14 日波動幅度，可用來估算停損距離。", "ATR% 過高代表波動大，追價風險也提高。")
        add("成交量 / 20日均量", f"{int(volume or 0):,} / {int(vol_ma20 or 0):,}", "成交量使用股數；量比大於 1 代表高於 20 日均量。", "放量上漲偏正面；放量跌破支撐是風險訊號。")
        add("20日量比", f"{fmt(volume_ratio_20)} 倍", "量比為當日成交量除以 20 日均量。", "量比低於 1 代表確認度不足；高於 1.5 且下跌要留意出貨或破位。")
        add("OBV", f"{int(obv or 0):,}", "OBV 用量價方向估計資金累積或流出。", "價格上漲但 OBV 不上升，代表量能背離風險。")
        add("前10日低點", fmt(previous_10d_low), "短線防守點，用來判斷是否破位。", "收盤跌破且無法站回，短線支撐可能轉壓力。")
        add("前20日高點", fmt(previous_20d_high), "短線突破觀察位。", "突破失敗跌回，容易形成假突破賣壓。")
        add("前60日高點", fmt(previous_60d_high), "波段壓力與突破觀察位。", "突破後應用 ATR 或上方壓力估算下一目標。")
        return {"items": items, "date": str(last.get("date")), "note": "技術數據只顯示數據與解讀，不顯示內部分數。"}
    except Exception as exc:
        return {"items": [{"name": "技術資料狀態", "value": "計算失敗", "explain": "技術細項計算時發生錯誤。", "risk": "請重新更新日K；錯誤：" + safe_error(exc)}], "note": "技術細項計算失敗：" + safe_error(exc)}


def build_technical_hints(rows_asc: list[dict[str, Any]], tech: dict[str, Any] | None = None) -> list[str]:
    # Display-only hints.  These do not feed scoring, main status, or outlook weights.
    if not rows_asc or calculate_indicators is None:
        return []
    try:
        df = _indicator_input_frame(rows_asc)
        ind = calculate_indicators(df)
        if len(ind) < 2:
            return []
        last = ind.iloc[-1]
        prev = ind.iloc[-2]

        def finite(row: Any, col: str) -> float | None:
            try:
                value = float(row.get(col))
                return value if math.isfinite(value) else None
            except Exception:
                return None

        close = finite(last, "close")
        prev_close = finite(prev, "close")
        ma20 = finite(last, "ma20")
        ma60 = finite(last, "ma60")
        rsi14 = finite(last, "rsi14")
        osc = finite(last, "osc")
        prev_osc = finite(prev, "osc")
        volume = finite(last, "volume")
        vol_ma20 = finite(last, "vol_ma20")
        volume_ratio_20 = (volume / vol_ma20) if volume is not None and vol_ma20 not in (None, 0) else None
        hints: list[str] = []
        if rsi14 is not None and ma20 is not None and ma60 is not None and 52 <= rsi14 <= 68 and ma20 >= ma60:
            hints.append("RSI 位於健康多頭區，未見明顯過熱。")
        if rsi14 is not None and rsi14 < 45:
            hints.append("RSI 轉弱，短線買盤動能不足。")
        if osc is not None and prev_osc is not None and osc < 0:
            if osc > prev_osc:
                hints.append("MACD 負柱收斂，下跌動能有減緩跡象。")
            elif osc < prev_osc:
                hints.append("MACD 負柱擴大，短線動能轉弱。")
        recent = ind.tail(3)
        if len(recent) >= 3:
            k_vals = [finite(row, "k") for _, row in recent.iterrows()]
            d_vals = [finite(row, "d") for _, row in recent.iterrows()]
            if all(v is not None and v > 80 for v in k_vals + d_vals):
                hints.append("KD 高檔鈍化，趨勢可能延續但追價風險提高。")
            if all(v is not None and v < 20 for v in k_vals + d_vals):
                hints.append("KD 低檔鈍化，弱勢延續，需等轉強確認。")
        if close is not None and prev_close is not None and volume_ratio_20 is not None:
            if close > prev_close and volume_ratio_20 < 0.8:
                hints.append("價漲量縮，反彈尚未獲量能確認。")
            if close < prev_close and volume_ratio_20 > 1.2:
                hints.append("價跌量增，賣壓放大。")
        return hints[:5]
    except Exception:
        logging.exception("Failed to build technical hints")
        return []


def explain_us_related_market(us_assets: list[dict[str, Any]]) -> list[str]:
    ok = [x for x in us_assets if x.get("quote", {}).get("ok")]
    if not ok:
        return ["美股即時/最近收盤資料暫時抓不到，先不要用美股區塊做判斷。"]
    up = [x for x in ok if (x.get("quote", {}).get("change_pct") or 0) > 0]
    down = [x for x in ok if (x.get("quote", {}).get("change_pct") or 0) < 0]
    semis = [x for x in ok if x.get("ticker") in {"TSM", "NVDA", "AMD", "ASML", "AVGO", "MRVL", "QCOM", "SMH", "SOXX"}]
    avg = None
    if semis:
        vals = [x["quote"].get("change_pct") for x in semis if x["quote"].get("change_pct") is not None]
        if vals:
            avg = sum(vals) / len(vals)
    notes = []
    notes.append(f"相關美股上漲 {len(up)} 檔、下跌 {len(down)} 檔；這是海外情緒參考，不等於台股隔日必漲跌。")
    if avg is not None:
        if avg >= 1.0:
            notes.append("半導體/AI 相關美股平均漲幅偏強，對台股同族群情緒偏正面，但若台股本身已過熱仍要小心追高。")
        elif avg <= -1.0:
            notes.append("半導體/AI 相關美股平均跌幅偏弱，台股相關個股容易有開低或賣壓風險。")
        else:
            notes.append("半導體/AI 相關美股平均變動不大，台股個股仍應回到自身技術位、籌碼與支撐賣壓判斷。")
    return notes


def _clean_stock_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"null", "none", "undefined", "nan"}:
        return ""
    return _shorten_legal_stock_name(text)


def _shorten_legal_stock_name(text: str) -> str:
    name = str(text or "").strip()
    for suffix in ("股份有限公司", "有限公司", "公司"):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    # Common TWSE/TPEx display convention: official legal names ending in
    # "科技" are often shortened to "科" in quote lists, e.g. 昇達科技 -> 昇達科.
    if len(name) > 3 and name.endswith("科技"):
        name = name[:-2] + "科"
    return name


def find_stock_item(code: str) -> dict[str, str] | None:
    code = str(code).strip().zfill(4)[:4]
    if not code.isdigit():
        return None
    name = ""
    watch_item = get_watchlist_item(code)
    if watch_item:
        name = _clean_stock_name(watch_item.get("name"))
    with closing(db()) as conn:
        if not name:
            row = conn.execute("SELECT code,name FROM eod_price WHERE code=? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
            if row:
                name = _clean_stock_name(row["name"])
        if not name:
            try:
                row = conn.execute("SELECT name FROM stock_industry_profile WHERE code=? LIMIT 1", (code,)).fetchone()
            except sqlite3.OperationalError:
                row = None
            if row:
                name = _clean_stock_name(row["name"])
    for item in read_components():
        if item.get("code") == code and not name:
            name = _clean_stock_name(item.get("name"))
            break
    if not name:
        resolved = resolve_stock(code)
        if resolved:
            name = _clean_stock_name(resolved.get("name"))
    if watch_item or name:
        return {"code": code, "name": name}
    return None
