from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from core.config import (
    OFFICIAL_TAIWAN_EXTRAORDINARY_CLOSURES,
    OFFICIAL_TAIWAN_EXTRAORDINARY_CLOSURE_URLS,
    OFFICIAL_TAIWAN_MARKET_HOLIDAY_YEARS,
    TWSE_HOLIDAY_SCHEDULE_HISTORY_URL,
    TWSE_HOLIDAY_SCHEDULE_URL,
    safe_error,
)
from core.http import request_json
from core.market_calendar_cache import (
    TWSE_CALENDAR_CACHE_PATH,
    load_twse_calendar_cache,
    taiwan_market_day_status,
)
from core.utils import now_tpe, normalize_date


try:
    import truststore  # type: ignore
except Exception:
    truststore = None
else:
    try:
        truststore.inject_into_ssl()
    except Exception:
        logging.getLogger(__name__).exception("Failed to inject truststore for TWSE calendar adapter")


CALENDAR_SCHEMA_VERSION = 1
CALENDAR_SOURCE = "TWSE holidaySchedule OpenAPI"
HISTORICAL_CALENDAR_SOURCE = "TWSE holidaySchedule historical JSON"


def normalize_twse_roc_calendar_date(value: Any) -> str | None:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 7:
        try:
            year = int(digits[:3]) + 1911
            parsed = datetime.strptime(f"{year:04d}{digits[3:]}", "%Y%m%d")
            return parsed.date().isoformat()
        except (TypeError, ValueError):
            return None
    return normalize_date(text)


def _is_closed_schedule_entry(name: str, description: str) -> bool:
    text = f"{name} {description}".strip()
    if "市場無交易" in text:
        return True
    if "開始交易" in text or "最後交易" in text:
        return False
    return True


def _calendar_payload_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict) or str(payload.get("stat") or "").lower() != "ok":
        raise ValueError("TWSE holidaySchedule payload is neither OpenAPI rows nor a successful historical report")
    fields = [str(value or "").strip() for value in payload.get("fields") or []]
    rows: list[dict[str, Any]] = []
    for raw in payload.get("data") or []:
        if not isinstance(raw, list):
            continue
        mapped = {fields[index]: value for index, value in enumerate(raw) if index < len(fields)}
        rows.append(
            {
                "Date": mapped.get("日期") or mapped.get("Date"),
                "Name": mapped.get("名稱") or mapped.get("Name"),
                "Description": mapped.get("說明") or mapped.get("Description"),
            }
        )
    return rows


def parse_twse_holiday_schedule(
    payload: Any,
    *,
    fetched_at: str | None = None,
    source: str = CALENDAR_SOURCE,
    source_url: str = TWSE_HOLIDAY_SCHEDULE_URL,
) -> dict[str, Any]:
    raw_rows = _calendar_payload_rows(payload)
    entries: list[dict[str, Any]] = []
    years: set[int] = set()
    for raw in raw_rows:
        day = normalize_twse_roc_calendar_date(raw.get("Date") or raw.get("date"))
        if not day:
            continue
        name = str(raw.get("Name") or raw.get("name") or "").strip()
        description = str(raw.get("Description") or raw.get("description") or "").strip()
        is_closed = _is_closed_schedule_entry(name, description)
        years.add(int(day[:4]))
        entries.append(
            {
                "date": day,
                "name": name,
                "description": description,
                "is_closed": is_closed,
                "source": source,
                "source_url": source_url,
            }
        )
    if not entries or not years:
        raise ValueError("TWSE holidaySchedule payload has no usable dated entries")

    for day, reason in OFFICIAL_TAIWAN_EXTRAORDINARY_CLOSURES.items():
        if int(day[:4]) not in years:
            continue
        if any(entry["date"] == day for entry in entries):
            continue
        entries.append(
            {
                "date": day,
                "name": "臨時停止交易",
                "description": reason,
                "is_closed": True,
                "source": "TWSE extraordinary closure bulletin",
                "source_url": OFFICIAL_TAIWAN_EXTRAORDINARY_CLOSURE_URLS.get(day),
            }
        )

    entries.sort(key=lambda item: (str(item["date"]), str(item["name"])))
    return {
        "schema_version": CALENDAR_SCHEMA_VERSION,
        "source": source,
        "source_url": source_url,
        "fetched_at": fetched_at or now_tpe().isoformat(timespec="seconds"),
        "years": sorted(years),
        "closure_dates": sorted({str(item["date"]) for item in entries if item.get("is_closed")}),
        "open_reference_dates": sorted({str(item["date"]) for item in entries if not item.get("is_closed")}),
        "entries": entries,
    }


def merge_twse_calendar_snapshots(*snapshots: dict[str, Any] | None) -> dict[str, Any]:
    entries_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    fetched_values: list[str] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        fetched = str(snapshot.get("fetched_at") or "").strip()
        if fetched:
            fetched_values.append(fetched)
        for entry in snapshot.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            day = str(entry.get("date") or "").strip()
            name = str(entry.get("name") or "").strip()
            if day:
                entries_by_key[(day, name)] = dict(entry)
    entries = sorted(entries_by_key.values(), key=lambda item: (str(item.get("date")), str(item.get("name"))))
    years = sorted({int(str(item["date"])[:4]) for item in entries if str(item.get("date") or "")[:4].isdigit()})
    return {
        "schema_version": CALENDAR_SCHEMA_VERSION,
        "source": "TWSE official annual schedules",
        "source_url": TWSE_HOLIDAY_SCHEDULE_HISTORY_URL,
        "fetched_at": max(fetched_values, default=now_tpe().isoformat(timespec="seconds")),
        "years": years,
        "closure_dates": sorted({str(item["date"]) for item in entries if item.get("is_closed")}),
        "open_reference_dates": sorted({str(item["date"]) for item in entries if not item.get("is_closed")}),
        "entries": entries,
    }


def write_twse_calendar_cache(snapshot: dict[str, Any], path: Path | None = None) -> Path:
    cache_path = path or TWSE_CALENDAR_CACHE_PATH
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=f".{cache_path.name}.",
        suffix=".tmp",
        dir=cache_path.parent,
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, cache_path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return cache_path


def refresh_twse_holiday_cache(
    *,
    required_year: int | None = None,
    cache_path: Path | None = None,
    requester: Callable[..., Any] | None = None,
    write_cache: bool = True,
) -> dict[str, Any]:
    """Refresh the official annual schedule, falling back to cache/static data.

    The fallback never marks an unknown calendar year as verified.  Callers that
    require a write gate must also inspect ``day_status.verified``.
    """

    year = int(required_year or now_tpe().year)
    target_path = cache_path or TWSE_CALENDAR_CACHE_PATH
    get_json = requester or request_json
    try:
        historical = year != now_tpe().year
        source_url = TWSE_HOLIDAY_SCHEDULE_HISTORY_URL if historical else TWSE_HOLIDAY_SCHEDULE_URL
        request_options = {"retries": 2, "retry_wait": 1.5, "timeout": 15}
        if historical:
            request_options["params"] = {"response": "json", "date": str(year)}
        payload = get_json(source_url, **request_options)
        snapshot = parse_twse_holiday_schedule(
            payload,
            source=HISTORICAL_CALENDAR_SOURCE if historical else CALENDAR_SOURCE,
            source_url=source_url,
        )
        if year not in {int(value) for value in snapshot.get("years") or []}:
            raise RuntimeError(f"TWSE holidaySchedule does not contain required year {year}")
        snapshot = merge_twse_calendar_snapshots(load_twse_calendar_cache(target_path), snapshot)
        written_path = write_twse_calendar_cache(snapshot, target_path) if write_cache else target_path
        return {
            "ok": True,
            "status": "OK",
            "source": snapshot["source"],
            "years": snapshot["years"],
            "closure_count": len(snapshot["closure_dates"]),
            "cache_path": str(written_path),
            "cache_written": bool(write_cache),
            "snapshot": snapshot if not write_cache else None,
        }
    except Exception as exc:
        cached = load_twse_calendar_cache(target_path)
        cached_years = {
            int(value)
            for value in (cached or {}).get("years") or []
            if str(value).isdigit()
        }
        fallback_available = year in cached_years or year in OFFICIAL_TAIWAN_MARKET_HOLIDAY_YEARS
        day_status = taiwan_market_day_status(now_tpe().date(), cache_path=target_path)
        return {
            "ok": False,
            "status": "SOURCE_DELAYED" if fallback_available else "UNAVAILABLE",
            "source": str((cached or {}).get("source") or "bundled_schedule_fallback"),
            "years": sorted(cached_years),
            "required_year": year,
            "fallback_available": fallback_available,
            "cache_written": False,
            "day_status": day_status,
            "error": safe_error(exc),
        }
