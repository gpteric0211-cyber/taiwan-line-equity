from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


def now_tpe() -> datetime:
    return datetime.now(TAIPEI)


def today_iso() -> str:
    return now_tpe().date().isoformat()


def normalize_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(TAIPEI).date().isoformat() if value.tzinfo else value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip().replace("/", "-").replace(".", "-")
    if not s or s.lower() in {"nan", "none", "null", "--"}:
        return None
    s = s.replace("\ufeff", "").strip()
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.fullmatch(r"(\d{2,3})(\d{2})(\d{2})", s)
    if m:
        return f"{int(m.group(1)) + 1911:04d}-{m.group(2)}-{m.group(3)}"
    m = re.fullmatch(r"(\d{2,3})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{int(m.group(1)) + 1911:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    try:
        return datetime.fromisoformat(s[:19]).date().isoformat()
    except Exception:
        return None


def normalize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value.astimezone(TAIPEI) if value.tzinfo else value.replace(tzinfo=TAIPEI)
        return dt.isoformat(timespec="seconds")
    d = normalize_date(value)
    if d and len(str(value).strip()) <= 10:
        return f"{d}T00:00:00+08:00"
    s = str(value).strip().replace("/", "-")
    try:
        dt = datetime.fromisoformat(s)
        dt = dt.astimezone(TAIPEI) if dt.tzinfo else dt.replace(tzinfo=TAIPEI)
        return dt.isoformat(timespec="seconds")
    except Exception:
        return None


def western_to_roc_date(value: Any) -> str | None:
    d = normalize_date(value)
    if not d:
        return None
    y, m, day = d.split("-")
    return f"{int(y) - 1911:03d}/{m}/{day}"


def parse_source_date(value: Any) -> str | None:
    return normalize_date(value)


def safe_date_compare(left: Any, right: Any) -> int | None:
    l = normalize_date(left)
    r = normalize_date(right)
    if not l or not r:
        return None
    return (date.fromisoformat(l) > date.fromisoformat(r)) - (date.fromisoformat(l) < date.fromisoformat(r))


def trading_date_lag(latest: Any, expected: Any) -> int | None:
    l = normalize_date(latest)
    e = normalize_date(expected)
    if not l or not e:
        return None
    return (date.fromisoformat(e) - date.fromisoformat(l)).days


def iso_date_lag_days(anchor: Any, source: Any) -> int | None:
    a = normalize_date(anchor)
    s = normalize_date(source)
    if not a or not s:
        return None
    return (date.fromisoformat(a) - date.fromisoformat(s)).days
