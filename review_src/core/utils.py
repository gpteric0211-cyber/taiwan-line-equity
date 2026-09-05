from __future__ import annotations

from datetime import date
from typing import Any

from core.date_utils import TAIPEI, normalize_date, now_tpe, today_iso


def iso_date_lag_days(reference: str | None, actual: str | None) -> int | None:
    reference = normalize_date(reference)
    actual = normalize_date(actual)
    if not reference or not actual:
        return None
    try:
        return (date.fromisoformat(reference) - date.fromisoformat(actual)).days
    except Exception:
        return None


def date_is_fresh_enough(actual: str | None, reference: str | None, *, max_lag_days: int = 3) -> bool:
    lag = iso_date_lag_days(reference, actual)
    return lag is not None and lag <= max_lag_days


def parse_num(x: Any) -> float | None:
    if x is None:
        return None
    s = str(x).replace(",", "").replace("%", "").strip()
    if s[:1].upper() == "X":
        s = s[1:].strip()
    if s in {"", "--", "-", "null", "None", "N/A"}:
        return None
    try:
        return float(s)
    except Exception:
        return None


def fmt(v: Any, digits: int = 2) -> str:
    x = parse_num(v)
    if x is None:
        return "--"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    if abs(x - round(x)) < 1e-9:
        return f"{x:.0f}"
    return f"{x:.{digits}f}".rstrip("0").rstrip(".")
