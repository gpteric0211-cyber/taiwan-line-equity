from __future__ import annotations

import re
from contextlib import closing
from typing import Any

from core.components import read_components
from core.db import db

LISTED_MARKET_ALIASES = {
    "twse",
    "tse",
    "listed",
    "上市",
    "上市股票",
    "上市公司",
    "ÉÏÊÐ",
}
OTC_MARKET_ALIASES = {
    "tpex",
    "otc",
    "two",
    "上櫃",
    "上櫃股票",
    "上櫃公司",
    "ÉÏÌñ",
}


def normalize_stock_code(value: Any) -> str | None:
    s = str(value or "").strip()
    if re.fullmatch(r"\d{4}", s):
        return s
    return None


def normalize_market_type(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return "unknown"
    low = s.lower()
    if s in LISTED_MARKET_ALIASES or low in LISTED_MARKET_ALIASES:
        return "listed"
    if s in OTC_MARKET_ALIASES or low in OTC_MARKET_ALIASES:
        return "otc"
    if "上櫃" in s or "tpex" in low or "otc" in low:
        return "otc"
    if "上市" in s or "twse" in low or low == "tse":
        return "listed"
    return "unknown"


def yahoo_symbols_for_code(code: Any, market_type: str | None = None) -> list[str]:
    clean = str(code or "").strip().zfill(4)[:4]
    mt = normalize_market_type(market_type)
    if mt == "listed":
        return [f"{clean}.TW"]
    if mt == "otc":
        return [f"{clean}.TWO"]
    return [f"{clean}.TW", f"{clean}.TWO"]


def resolve_yahoo_symbol(code: Any, market_type: str | None = None) -> str:
    return yahoo_symbols_for_code(code, market_type)[0]


def _market_from_stock_industry_profile(code: str) -> dict[str, Any] | None:
    with closing(db()) as conn:
        try:
            row = conn.execute(
                """
                SELECT code,name,market,source,quality,updated_at
                FROM stock_industry_profile
                WHERE code=?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (code,),
            ).fetchone()
        except Exception:
            row = None
        if not row:
            return None
        market_type = normalize_market_type(row["market"])
        return {
            "code": code,
            "name": row["name"],
            "market_raw": row["market"],
            "market_type": market_type,
            "exchange": "TPEX" if market_type == "otc" else ("TWSE" if market_type == "listed" else None),
            "source": row["source"],
            "quality": row["quality"],
            "updated_at": row["updated_at"],
        }


def resolve_market_profile(code: Any) -> dict[str, Any]:
    clean = normalize_stock_code(str(code or "").strip().zfill(4)[:4])
    if not clean:
        return {
            "code": str(code or "").strip(),
            "market_type": "unknown",
            "exchange": None,
            "yahoo_symbol": None,
            "yahoo_symbols": [],
            "fugle_market": None,
            "bootstrap_supported": False,
            "unsupported_reason": "invalid_code",
        }
    profile = _market_from_stock_industry_profile(clean) or {"code": clean, "market_type": "unknown", "exchange": None}
    if any(str(item.get("code", "")).zfill(4)[:4] == clean for item in read_components()):
        profile["market_type"] = "listed"
        profile["exchange"] = "TWSE"
        profile.setdefault("source", "taiwan50_components")
    mt = normalize_market_type(profile.get("market_type"))
    symbols = yahoo_symbols_for_code(clean, mt)
    profile.update({
        "code": clean,
        "market_type": mt,
        "exchange": profile.get("exchange") or ("TPEX" if mt == "otc" else ("TWSE" if mt == "listed" else None)),
        "yahoo_symbol": symbols[0] if symbols else None,
        "yahoo_symbols": symbols,
        "fugle_market": "TPEX" if mt == "otc" else ("TWSE" if mt == "listed" else None),
        "bootstrap_supported": mt in {"listed", "otc", "unknown"},
        "unsupported_reason": None if mt in {"listed", "otc", "unknown"} else "unsupported_market",
    })
    return profile
