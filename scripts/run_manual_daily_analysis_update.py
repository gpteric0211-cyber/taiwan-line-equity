from __future__ import annotations

"""Run the complete manual post-close analysis-data update on one database."""

import argparse
import json
import sys
import threading
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
for path in (ROOT, REVIEW_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core.config import DB_PATH  # noqa: E402
from core.db import db  # noqa: E402
from core.market_session import (  # noqa: E402
    is_taiwan_trading_day,
    recent_market_date_for_post_close,
    now_tpe,
)
from repository.market_analytics_repository import active_stock_codes  # noqa: E402
from scripts.run_post_close_daily_pipeline import (  # noqa: E402
    capture_progress,
    record_unavailable_capture_attempts,
    run_capture_pipeline,
    run_finalize_pipeline,
)
from scripts.replay_fugle_cached_payloads import replay_cached_payloads  # noqa: E402
from scripts.update_all_market_database import (  # noqa: E402
    official_update_exit_code,
    run_all_market_update,
)
from scripts.verify_daily_analysis_update import (  # noqa: E402
    build_daily_analysis_report, _batch_publication, _publication_complete,
    _table_exists,
)
from services.external_event_service import refresh_external_market_events  # noqa: E402
from services.daily_chip_momentum_service import (  # noqa: E402
    refresh_daily_chip_momentum_for_codes,
)
from services.taiwan50_close_batch import update_taiwan50_close_batch  # noqa: E402
from services.tdcc_equity_concentration_service import (  # noqa: E402
    update_tdcc_equity_concentration_for_codes,
)


REPORT_PATH = ROOT / "docs" / "MANUAL_DAILY_ANALYSIS_UPDATE_REPORT.json"
FUGLE_CACHE_DIR = ROOT / "logs" / "fugle_intraday_update_json"


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _dedupe(values: list[str]) -> list[str]:
    return sorted({str(value).strip().zfill(4) for value in values if str(value).strip()})


def _run_step(
    *,
    index: int,
    total: int,
    name: str,
    action: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    print(f"\n[{index}/{total}] 開始：{name}", flush=True)
    started = datetime.now().astimezone()
    try:
        detail = action()
        ok = bool(detail.get("ok"))
        result = {
            "name": name,
            "ok": ok,
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "detail": detail,
        }
    except Exception as exc:
        result = {
            "name": name,
            "ok": False,
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "error_class": type(exc).__name__,
            "error": str(exc),
        }
    label = "完成" if result["ok"] else "失敗"
    print(f"[{index}/{total}] {label}：{name}", flush=True)
    return result


def _capture(trade_date: str) -> dict[str, Any]:
    stopped = threading.Event()

    def report_progress() -> None:
        while not stopped.wait(30):
            try:
                progress = capture_progress(trade_date)
                attempted = int(progress.get("attempted_stocks") or 0)
                required = int(progress.get("required_capture_stocks") or 0)
                percentage = attempted / required * 100 if required else 0.0
                print(
                    f"[分價量進度] {attempted}/{required} "
                    f"({percentage:.1f}%)",
                    flush=True,
                )
            except Exception as exc:
                print(f"[分價量進度] 暫時無法讀取：{exc}", flush=True)

    monitor = threading.Thread(target=report_progress, daemon=True)
    monitor.start()
    try:
        code = int(run_capture_pipeline(trade_date, window_end="23:59"))
    finally:
        stopped.set()
        monitor.join(timeout=2)
    progress = capture_progress(trade_date)
    return {
        # Exit 4/5 is retryable at capture time and can include legitimate
        # no-trade stocks.  The following official stage classifies those
        # stocks and the final verifier remains the publication gate.
        "ok": code in {0, 4, 5},
        "exit_code": code,
        "trade_date": trade_date,
        "capture_progress": progress,
        "capture_stage_ready": bool(code == 0 and trade_date == now_tpe().date().isoformat()),
        "historical_capture_skipped": trade_date != now_tpe().date().isoformat(),
        "deferred_to_official_verification": code in {4, 5},
    }


def _replay_cached_capture(trade_date: str) -> dict[str, Any]:
    trade_codes = {
        path.name.split("_")[1]
        for path in FUGLE_CACHE_DIR.glob(f"{trade_date}_*_trades.json")
        if len(path.name.split("_")) >= 3
    }
    volume_codes = {
        path.name.split("_")[1]
        for path in FUGLE_CACHE_DIR.glob(f"{trade_date}_*_volumes.json")
        if len(path.name.split("_")) >= 3
    }
    codes = sorted(trade_codes & volume_codes)
    if not codes:
        return {
            "ok": True,
            "status": "skipped_no_same_date_cache",
            "trade_date": trade_date,
            "selected_code_count": 0,
        }
    print(f"[快取重播] 發現 {len(codes)} 檔同日 Fugle 原始資料", flush=True)
    result = replay_cached_payloads(
        FUGLE_CACHE_DIR,
        trade_date,
        codes=codes,
        dry_run=False,
    )
    failed_codes = [str(code) for code in result.get("failed_codes") or []]
    unavailable_evidence_written = record_unavailable_capture_attempts(
        trade_date,
        failed_codes,
    )
    return {
        **result,
        # A malformed/stale cache entry is not fatal.  It remains explicitly
        # unavailable and must never be promoted to a no-trade classification
        # without corroborating official evidence.
        "ok": True,
        "cache_replay_ready": bool(result.get("ok")),
        "deferred_to_network_capture": bool(result.get("failed_code_count")),
        "unavailable_evidence_written": unavailable_evidence_written,
        "source": "persisted_same_date_fugle_json",
    }


def _missing_official_dates(trade_date: str) -> list[str]:
    with closing(db()) as conn:
        row = conn.execute(
            """
            SELECT MAX(date)
            FROM history_price
            WHERE date < ? AND (LOWER(COALESCE(source_quality,''))='official'
               OR UPPER(COALESCE(source,'')) LIKE '%TWSE%'
               OR UPPER(COALESCE(source,'')) LIKE '%TPEX%')
            """, (trade_date,)
        ).fetchone()
        latest = str(row[0]) if row and row[0] else ""
        # Partial rows do not advance the catch-up cursor. Start after the last
        # fully classified publication, rather than MAX(history_price.date).
        if _table_exists(conn, "full_market_batch_publications"):
            dates = conn.execute(
                "SELECT trade_date FROM full_market_batch_publications "
                "WHERE trade_date < ? ORDER BY trade_date DESC", (trade_date,)
            ).fetchall()
            complete = next((str(item[0]) for item in dates if _publication_complete(
                _batch_publication(conn, str(item[0]))
            )), None)
            if complete:
                latest = complete
            elif latest:
                latest = (date.fromisoformat(latest) - timedelta(days=1)).isoformat()
        elif latest:
            latest = (date.fromisoformat(latest) - timedelta(days=1)).isoformat()
    if not latest:
        raise RuntimeError("No official history baseline; run the explicit history bootstrap first")
    cursor = date.fromisoformat(latest) + timedelta(days=1)
    target = date.fromisoformat(trade_date)
    missing: list[str] = []
    while cursor < target:
        if is_taiwan_trading_day(cursor):
            missing.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return missing


def _official_catch_up(trade_date: str) -> dict[str, Any]:
    missing_dates = _missing_official_dates(trade_date)
    results: list[dict[str, Any]] = []
    for missing_date in missing_dates:
        print(f"[補日] 更新缺少的官方交易日：{missing_date}", flush=True)
        result = run_all_market_update(run_date=missing_date, dry_run=False)
        exit_code = int(official_update_exit_code(result))
        results.append({
            "trade_date": missing_date,
            "ok": exit_code == 0,
            "exit_code": exit_code,
            "official_core_ready": bool(result.get("official_core_ready")),
            "status": result.get("status"),
        })
        if exit_code not in {0, 4, 5}:
            break
    fatal_results = [item for item in results if item["exit_code"] not in {0, 4, 5}]
    all_ready = all(item["ok"] for item in results)
    return {
        "ok": not fatal_results,
        "target_date": trade_date,
        "missing_dates": missing_dates,
        "updated_dates": results,
        "catch_up_ready": all_ready,
        "deferred_to_final_verification": bool(results and not all_ready),
        "status": "up_to_date" if not missing_dates else "complete" if all_ready else "partial",
    }


def _official_finalize(trade_date: str) -> dict[str, Any]:
    code = int(run_finalize_pipeline(trade_date))
    return {
        "ok": code in {0, 4, 5},
        "exit_code": code,
        "trade_date": trade_date,
        "official_stage_ready": code == 0,
        "deferred_to_final_verification": code in {4, 5},
    }


def _daily_chip(trade_date: str) -> dict[str, Any]:
    with closing(db()) as conn:
        all_codes = _dedupe(active_stock_codes(conn))
    result = refresh_daily_chip_momentum_for_codes(
        all_codes,
        date=trade_date,
        mode="tw50",
        dry_run=False,
    )
    failed_count = int(result.get("failed_count") or 0)
    written = int(result.get("write_count") or 0)
    return {
        **result,
        "ok": bool(all_codes and failed_count == 0 and written >= len(all_codes)),
        "expected_codes": len(all_codes),
        "scope": "all_active_listed_and_otc_stocks",
    }


def _taiwan50_close_batch(trade_date: str) -> dict[str, Any]:
    result = update_taiwan50_close_batch(data_date=trade_date, dry_run=False)
    return {
        **result,
        "ok": bool(
            result.get("data_date") == trade_date
            and int(result.get("total_count") or 0) >= 50
            and int(result.get("error_count") or 0) == 0
        ),
    }


def _tdcc_weekly() -> dict[str, Any]:
    with closing(db()) as conn:
        codes = active_stock_codes(conn)
    result = update_tdcc_equity_concentration_for_codes(
        codes,
        source="tdcc",
        days=180,
        dry_run=False,
    )
    return {
        **result,
        "ok": bool(result.get("ok") and result.get("latest_date")),
        "frequency": "weekly",
    }


def _external_events(trade_date: str) -> dict[str, Any]:
    result = refresh_external_market_events(dry_run=False)
    receipt = {**result, "requested_trade_date": trade_date,
               "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    _write_report(ROOT / "docs" / "MANUAL_EXTERNAL_EVENTS_REPORT.json", receipt)
    # Preserve valid core updates if an optional feed is unavailable, but make
    # the final completeness verifier reject an incomplete required source.
    return {**receipt, "ok": True, "source_ready": bool(result.get("ok")),
            "deferred_to_final_verification": not bool(result.get("ok"))}


def run_manual_update(
    *,
    trade_date: str,
    report_path: Path = REPORT_PATH,
) -> dict[str, Any]:
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    total = 9
    steps: list[dict[str, Any]] = []
    def verify() -> dict[str, Any]:
        verification = build_daily_analysis_report(DB_PATH, trade_date=trade_date)
        return {
            **verification,
            "ok": bool(verification["safe_to_publish"]),
        }

    actions: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        ("補齊今日以前遺漏的官方交易日", lambda: _official_catch_up(trade_date)),
        ("重播已保存的同日 Fugle 原始資料", lambda: _replay_cached_capture(trade_date)),
        ("Fugle 全市場逐筆成交、分價量與內外盤擷取", lambda: _capture(trade_date)),
        ("官方收盤、估值、技術、法人、融資融券、借券與估算成本", lambda: _official_finalize(trade_date)),
        ("全市場每日籌碼動能", lambda: _daily_chip(trade_date)),
        ("台灣50收盤批次、支撐壓力與分價量輪廓", lambda: _taiwan50_close_batch(trade_date)),
        ("TDCC 集保股權分散週資料", _tdcc_weekly),
        ("官方月營收、政策事件及已設定的授權新聞", lambda: _external_events(trade_date)),
        ("今日分析資料完整度驗收", verify),
    ]
    for index, (name, action) in enumerate(actions, start=1):
        step = _run_step(index=index, total=total, name=name, action=action)
        steps.append(step)
        progress = {
            "status": "running" if step["ok"] else "failed",
            "trade_date": trade_date,
            "started_at": started_at,
            "completed_steps": index,
            "total_steps": total,
            "steps": steps,
        }
        _write_report(report_path, progress)
        if not step["ok"]:
            break

    ok = len(steps) == total and all(step["ok"] for step in steps)
    verification = (
        (steps[-1].get("detail") or {})
        if steps and steps[-1].get("name") == "今日分析資料完整度驗收"
        else {}
    )
    daily_update_complete = bool(verification.get("daily_update_complete"))
    result = {
        "ok": ok,
        "status": (
            "complete"
            if ok and daily_update_complete
            else "source_incomplete_safe_to_publish"
            if ok
            else "failed_closed"
        ),
        "daily_update_complete": daily_update_complete,
        "safe_to_publish": bool(verification.get("safe_to_publish")),
        "trade_date": trade_date,
        "database": str(DB_PATH),
        "isolated_candidate_expected": True,
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "completed_steps": len(steps),
        "total_steps": total,
        "steps": steps,
    }
    _write_report(report_path, result)
    print("\n" + json.dumps(result, ensure_ascii=False, indent=2, default=str), flush=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=recent_market_date_for_post_close())
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Print the manual update stages without network calls or DB writes.",
    )
    args = parser.parse_args(argv)
    if args.plan_only:
        print(json.dumps({
            "ok": True,
            "mode": "plan_only",
            "trade_date": args.date,
            "writes_db": False,
            "stages": [
                "missing_official_trading_day_catch_up",
                "same_date_fugle_cache_replay",
                "fugle_price_volume_capture",
                "official_close_and_analysis_inputs",
                "daily_chip_momentum",
                "taiwan50_close_batch",
                "tdcc_weekly",
                "monthly_revenue_policy_and_configured_news",
                "read_only_completeness_verification",
            ],
        }, ensure_ascii=False, indent=2))
        return 0
    result = run_manual_update(trade_date=args.date, report_path=args.report)
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
