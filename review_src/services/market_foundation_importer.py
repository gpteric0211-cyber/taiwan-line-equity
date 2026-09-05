from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from adapter.tpex import fetch_tpex_daily_close_quotes
from adapter.twse import fetch_twse_stock_day_all_rows
from adapter.yahoo import fetch_yahoo_time_sales
from adapter.yahoo_timesales import build_price_volume_distribution_from_time_sales
from core.data_map_generator import write_data_file_map
from core.db import db
from core.market_foundation_schema import (
    ensure_market_foundation_schema,
    prune_market_foundation_data,
    record_data_source_audit,
    upsert_daily_ohlcv,
    upsert_price_volume_distribution,
)


def normalize_market_foundation_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if text.isdigit() and len(text) < 4:
        return text.zfill(4)
    return text


def parse_codes(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = re.split(r"[,;\s]+", value)
    else:
        raw = value
    out: list[str] = []
    for item in raw:
        code = normalize_market_foundation_code(item)
        if code and re.fullmatch(r"\d{4}", code) and code not in out:
            out.append(code)
    return out


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _filter_items(items: list[dict[str, Any]], codes: set[str], date: str | None = None) -> list[dict[str, Any]]:
    out = []
    for item in items:
        code = normalize_market_foundation_code(item.get("code"))
        if codes and code not in codes:
            continue
        if date and item.get("date") and str(item.get("date")) != date:
            # Official all-market endpoints may publish latest available date.
            # Keep the row but let audit show the requested run_date.
            pass
        out.append(item)
    return out


def _write_audit(conn, payload: dict[str, Any]) -> None:
    record_data_source_audit(conn, payload)
    conn.commit()


def _source_date_status(sources: list[dict[str, Any]], target_date: str | None) -> dict[str, Any]:
    ok_sources = [item for item in sources if item.get("ok")]
    dates = {
        str(item.get("source") or ""): str(item.get("data_date") or "")
        for item in ok_sources
        if item.get("data_date")
    }
    if target_date:
        mismatched = {source: date for source, date in dates.items() if date != target_date}
        if mismatched:
            return {
                "ok": False,
                "status": "SOURCE_DELAYED",
                "reason": "official_source_date_mismatch_target",
                "target_date": target_date,
                "source_dates": dates,
                "mismatched_sources": mismatched,
            }
    unique_dates = {date for date in dates.values() if date}
    if len(unique_dates) > 1:
        latest = max(unique_dates)
        delayed = {source: date for source, date in dates.items() if date != latest}
        return {
            "ok": False,
            "status": "SOURCE_DELAYED",
            "reason": "official_sources_have_different_data_dates",
            "latest_official_date": latest,
            "source_dates": dates,
            "delayed_sources": delayed,
        }
    return {"ok": True, "status": "OK", "source_dates": dates}


def _official_rows(codes: set[str], target_date: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    audit: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    started = _now_text()
    twse = fetch_twse_stock_day_all_rows()
    started_tpex = _now_text()
    tpex = fetch_tpex_daily_close_quotes()
    source_meta = [twse, tpex]
    date_status = _source_date_status(source_meta, target_date)
    if not date_status.get("ok"):
        delayed_by_source = set(
            (date_status.get("delayed_sources") or date_status.get("mismatched_sources") or {}).keys()
        )
        for item in source_meta:
            source_name = str(item.get("source") or "")
            if not item.get("ok"):
                item["status"] = "FAILED"
            elif source_name in delayed_by_source:
                item["status"] = "SOURCE_DELAYED"
            else:
                item["status"] = "OK"
            item["delay_reason"] = date_status
    audit.append({
        "started_at": started,
        "finished_at": _now_text(),
        "run_date": target_date,
        "source": "TWSE_OFFICIAL",
        "status": twse.get("status") or ("OK" if twse.get("ok") else "FAILED"),
        "message": twse.get("error") or json.dumps(twse.get("delay_reason") or {}, ensure_ascii=False),
        "rows_read": twse.get("rows") or 0,
        "rows_written": 0,
        "payload_json": json.dumps({k: v for k, v in twse.items() if k != "items"}, ensure_ascii=False),
    })
    if date_status.get("ok") and twse.get("items"):
        rows.extend(_filter_items(twse["items"], codes, target_date))

    audit.append({
        "started_at": started_tpex,
        "finished_at": _now_text(),
        "run_date": target_date,
        "source": "TPEX_OFFICIAL",
        "status": tpex.get("status") or ("OK" if tpex.get("ok") else "FAILED"),
        "message": tpex.get("error") or json.dumps(tpex.get("delay_reason") or {}, ensure_ascii=False),
        "rows_read": tpex.get("rows") or 0,
        "rows_written": 0,
        "payload_json": json.dumps({k: v for k, v in tpex.items() if k != "items"}, ensure_ascii=False),
    })
    if date_status.get("ok"):
        for item in _filter_items(tpex.get("items") or [], codes, target_date):
            normalized = dict(item)
            normalized["source"] = "TPEX_OFFICIAL"
            normalized["source_quality"] = "OK"
            normalized["market"] = "otc"
            normalized.setdefault("volume_unit", "shares")
            rows.append(normalized)
    return rows, audit, source_meta


def _scraped_price_volume_rows(codes: list[str], target_date: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    audit: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for idx, code in enumerate(codes):
        started = _now_text()
        try:
            raw = fetch_yahoo_time_sales(code)
            source_rows = raw.get("rows") if isinstance(raw, dict) else []
            grouped = build_price_volume_distribution_from_time_sales(
                source_rows or [],
                code=code,
                trade_date=target_date,
                fetched_at=time.time(),
            )
            rows.extend(grouped)
            audit.append({
                "started_at": started,
                "finished_at": _now_text(),
                "run_date": target_date,
                "code": code,
                "source": "YAHOO",
                "status": "PARTIAL" if grouped else "FAILED",
                "message": "" if grouped else str(raw.get("reason") or raw.get("error") or "no rows"),
                "rows_read": len(source_rows or []),
                "rows_written": 0,
                "payload_json": json.dumps({"source_status": raw.get("source_status"), "source_type": raw.get("source_type")}, ensure_ascii=False),
            })
        except Exception as exc:
            audit.append({
                "started_at": started,
                "finished_at": _now_text(),
                "run_date": target_date,
                "code": code,
                "source": "YAHOO",
                "status": "FAILED",
                "message": str(exc),
                "rows_read": 0,
                "rows_written": 0,
                "payload_json": "{}",
            })
        if idx < len(codes) - 1:
            time.sleep(1.0)
    return rows, audit


def run_market_foundation_update(
    *,
    run_date: str | None = None,
    codes: list[str] | None = None,
    official_only: bool = False,
    include_scraped: bool = False,
    allow_full_scrape: bool = False,
    dry_run: bool = False,
    generate_data_map: bool = True,
    data_map_path: Path | None = None,
    prune: bool = True,
) -> dict[str, Any]:
    clean_codes = parse_codes(codes or [])
    warnings: list[str] = []
    if official_only and include_scraped:
        warnings.append("--official-only overrides --include-scraped; supplemental Yahoo/PChome sources were skipped.")
        include_scraped = False
    if include_scraped and not clean_codes and not allow_full_scrape:
        return {
            "ok": False,
            "refused": True,
            "writes_db": False,
            "reason": "--include-scraped requires --codes or --allow-full-scrape.",
            "warnings": warnings,
        }

    started_at = _now_text()
    result: dict[str, Any] = {
        "ok": True,
        "started_at": started_at,
        "finished_at": None,
        "run_date": run_date,
        "codes": clean_codes,
        "official_only": official_only,
        "include_scraped": include_scraped,
        "allow_full_scrape": allow_full_scrape,
        "dry_run": dry_run,
        "warnings": warnings,
        "writes_db": False,
        "official_rows": 0,
        "official_written": 0,
        "scraped_distribution_rows": 0,
        "scraped_distribution_written": 0,
        "audit_rows": 0,
        "prune": None,
        "data_map": None,
    }
    codes_set = set(clean_codes)
    official_rows, official_audit, source_meta = _official_rows(codes_set, run_date)
    result["official_sources"] = [{k: v for k, v in meta.items() if k != "items"} for meta in source_meta]
    result["official_rows"] = len(official_rows)
    delayed_sources = [
        item for item in result["official_sources"]
        if str(item.get("status") or "") == "SOURCE_DELAYED"
    ]
    if delayed_sources:
        result["ok"] = False
        result["status"] = "SOURCE_DELAYED"
        result["source_delayed"] = True
        result["warnings"].append("Official source dates are not aligned; DB write skipped.")
        result["finished_at"] = _now_text()
        return result

    with db() as conn:
        if dry_run:
            result["schema"] = {"dry_run": True, "schema_write": "skipped"}
        else:
            result["schema"] = ensure_market_foundation_schema(conn)
        now = time.time()
        if not dry_run:
            for row in official_rows:
                row = dict(row)
                row.setdefault("updated_at", now)
                row.setdefault("fetched_at", now)
                if upsert_daily_ohlcv(conn, row):
                    result["official_written"] += 1
            result["writes_db"] = result["official_written"] > 0

        scraped_rows: list[dict[str, Any]] = []
        scraped_audit: list[dict[str, Any]] = []
        if include_scraped:
            scrape_codes = clean_codes
            scraped_rows, scraped_audit = _scraped_price_volume_rows(scrape_codes, run_date)
            result["scraped_distribution_rows"] = len(scraped_rows)
            if not dry_run:
                for row in scraped_rows:
                    if upsert_price_volume_distribution(conn, row):
                        result["scraped_distribution_written"] += 1
                result["writes_db"] = result["writes_db"] or result["scraped_distribution_written"] > 0

        for payload in official_audit + scraped_audit:
            payload = dict(payload)
            payload.setdefault("mode", "market_foundation")
            payload["rows_written"] = 0
            if not dry_run:
                _write_audit(conn, payload)
                result["audit_rows"] += 1
                result["writes_db"] = True
        if not dry_run and prune:
            result["prune"] = prune_market_foundation_data(conn)
        elif not dry_run:
            result["prune"] = {"skipped": True, "reason": "prune_disabled"}
        conn.commit()
        if generate_data_map and not result.get("refused"):
            path = data_map_path or Path("docs") / "DATA_FILE_MAP.txt"
            write_data_file_map(conn, path)
            result["data_map"] = str(path)
    result["finished_at"] = _now_text()
    if result["official_rows"] == 0 and not result["scraped_distribution_rows"]:
        result["ok"] = False
        result["status"] = "PARTIAL"
    else:
        result["status"] = "OK" if not dry_run else "DRY_RUN"
    return result
