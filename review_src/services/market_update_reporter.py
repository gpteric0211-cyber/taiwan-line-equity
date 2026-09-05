from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "logs" / "market_foundation"
REPORT_ARCHIVE_DIR = LOG_DIR / "reports"
DAILY_REPORT = ROOT / "docs" / "DAILY_UPDATE_REPORT.txt"
BACKFILL_REPORT = ROOT / "docs" / "BACKFILL_UPDATE_REPORT.txt"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_market_log_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def write_text_atomic(path: Path | str, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)


def append_log(path: Path | str | None, message: str, payload: dict[str, Any] | None = None) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = f"{now_text()} {message}"
    if payload:
        line += " " + json.dumps(payload, ensure_ascii=False, default=str)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def progress_line(
    *,
    phase_name: str,
    current_step: int,
    total_steps: int,
    status: str,
    current_source: str = "",
    current_date: str | None = None,
    fetched_rows: int = 0,
    valid_rows: int = 0,
    written_rows: int = 0,
    skipped_rows: int = 0,
    failed_rows: int = 0,
) -> str:
    total = max(int(total_steps or 1), 1)
    step = min(max(int(current_step or 0), 0), total)
    pct = int(round((step / total) * 100))
    filled = min(10, max(0, pct // 10))
    bar = "#" * filled + "-" * (10 - filled)
    parts = [
        f"[{bar}] {pct}%",
        phase_name,
        f"source={current_source or '-'}",
        f"date={current_date or '-'}",
        f"fetched={fetched_rows}",
        f"valid={valid_rows}",
        f"written={written_rows}",
        f"skipped={skipped_rows}",
        f"failed={failed_rows}",
        f"status={status}",
    ]
    return " ".join(parts)


def emit_progress(*, quiet: bool, log_file: Path | str | None, **payload: Any) -> None:
    line = progress_line(**payload)
    if not quiet:
        print(line)
    append_log(log_file, "PROGRESS", payload)


def _source_status(result: dict[str, Any], source: str) -> tuple[str, int]:
    aliases = {
        "TWSE_OFFICIAL": {"TWSE_OFFICIAL"},
        "TPEX_OFFICIAL": {"TPEX_OFFICIAL", "TPEX OpenAPI", "TPEX_OFFICIAL"},
    }.get(source, {source})
    for item in result.get("official_sources") or []:
        if item.get("source") in aliases:
            status = str(item.get("status") or ("OK" if item.get("ok") is True else "UNKNOWN"))
            return status, int(item.get("valid_rows") or item.get("rows") or item.get("rows_read") or 0)
    return "SKIPPED", 0


def summarize_daily_result(result: dict[str, Any], *, exit_code: int, log_file: Path | str | None) -> dict[str, Any]:
    twse_status, twse_rows = _source_status(result, "TWSE_OFFICIAL")
    tpex_status, tpex_rows = _source_status(result, "TPEX_OFFICIAL")
    official_statuses = [twse_status, tpex_status]
    completed = sum(1 for item in official_statuses if item == "OK")
    total = len(official_statuses)
    missing = [name for name, status in (("TWSE_OFFICIAL", twse_status), ("TPEX_OFFICIAL", tpex_status)) if status != "OK"]
    status = str(result.get("status") or ("SUCCESS" if exit_code == 0 else "FAILED"))
    if exit_code == 4:
        status = "PARTIAL_RETRYABLE"
    elif exit_code == 5:
        status = "SOURCE_DELAYED_RETRYABLE"
    elif exit_code == 1:
        status = "CLI_ERROR"
    elif exit_code == 2:
        status = "FATAL_ERROR"
    elif exit_code == 3:
        status = "SAFETY_ABORT"
    return {
        "overall_status": status,
        "exit_code": exit_code,
        "completeness_pct": round((completed / total) * 100, 2) if total else 0,
        "official_sources_completed": completed,
        "official_sources_total": total,
        "twse_status": twse_status,
        "twse_rows": twse_rows,
        "tpex_status": tpex_status,
        "tpex_rows": tpex_rows,
        "audit_status": "SKIPPED" if result.get("dry_run") else ("OK" if result.get("audit_rows", 0) else "PARTIAL"),
        "prune_status": "SKIPPED" if result.get("dry_run") else ("OK" if result.get("prune") is not None else "PARTIAL"),
        "missing_or_delayed_sources": missing,
        "latest_successful_update_time": result.get("finished_at") if exit_code == 0 else "",
        "log_file": str(log_file or ""),
        "warnings": result.get("warnings") or [],
        "writes_db": bool(result.get("writes_db")),
        "official_rows": int(result.get("official_rows") or 0),
        "official_written": int(result.get("official_written") or 0),
        "scraped_distribution_rows": int(result.get("scraped_distribution_rows") or 0),
        "scraped_distribution_written": int(result.get("scraped_distribution_written") or 0),
    }


def render_daily_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Daily Market Foundation Update Report",
        "",
        "This file is overwritten by explicit CLI or scheduler runs. Committed copies must remain placeholders without runtime timestamps, row counts, or log paths.",
        "",
    ]
    for key in [
        "overall_status",
        "exit_code",
        "completeness_pct",
        "official_sources_completed",
        "official_sources_total",
        "twse_status",
        "twse_rows",
        "tpex_status",
        "tpex_rows",
        "audit_status",
        "prune_status",
        "missing_or_delayed_sources",
        "latest_successful_update_time",
        "log_file",
        "warnings",
        "writes_db",
        "official_rows",
        "official_written",
        "scraped_distribution_rows",
        "scraped_distribution_written",
    ]:
        value = summary.get(key, "")
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False, default=str)
        lines.append(f"{key}: {value}")
    lines.append("")
    return "\n".join(lines)


def write_daily_report(result: dict[str, Any], *, exit_code: int, report_file: Path | str | None, log_file: Path | str | None) -> dict[str, Any]:
    summary = summarize_daily_result(result, exit_code=exit_code, log_file=log_file)
    write_text_atomic(Path(report_file) if report_file else DAILY_REPORT, render_daily_report(summary))
    return summary


def render_backfill_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Backfill Market Foundation Update Report",
        "",
        "This file is overwritten by explicit backfill runs. Committed copies must remain placeholders without runtime timestamps, row counts, or log paths.",
        "",
    ]
    for key in [
        "overall_status",
        "target_trading_days",
        "completed_trading_days",
        "completed_pct",
        "partial_dates_count",
        "failed_dates_count",
        "skipped_dates_count",
        "remaining_dates_count",
        "current_batch_range",
        "last_success_date",
        "date_source",
        "data_completeness_status",
        "writes_db",
        "dry_run",
        "log_file",
        "state_file",
    ]:
        value = summary.get(key, "")
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False, default=str)
        lines.append(f"{key}: {value}")
    lines.append("")
    return "\n".join(lines)


def write_backfill_report(summary: dict[str, Any], *, report_file: Path | str | None = None) -> None:
    write_text_atomic(Path(report_file) if report_file else BACKFILL_REPORT, render_backfill_report(summary))


def daily_placeholder_text() -> str:
    keys = [
        "overall_status",
        "exit_code",
        "completeness_pct",
        "official_sources_completed",
        "official_sources_total",
        "twse_status",
        "twse_rows",
        "tpex_status",
        "tpex_rows",
        "audit_status",
        "prune_status",
        "missing_or_delayed_sources",
        "latest_successful_update_time",
        "log_file",
        "warnings",
        "writes_db",
    ]
    return "# Daily Market Foundation Update Report\n\nPlaceholder template. Runtime reports are generated by explicit CLI or scheduler runs and must not be committed.\n\n" + "\n".join(f"{key}: " for key in keys) + "\n"


def backfill_placeholder_text() -> str:
    keys = [
        "overall_status",
        "target_trading_days",
        "completed_trading_days",
        "completed_pct",
        "partial_dates_count",
        "failed_dates_count",
        "skipped_dates_count",
        "remaining_dates_count",
        "current_batch_range",
        "last_success_date",
        "date_source",
        "data_completeness_status",
        "writes_db",
        "dry_run",
    ]
    return "# Backfill Market Foundation Update Report\n\nPlaceholder template. Runtime reports are generated by explicit backfill runs and must not be committed.\n\n" + "\n".join(f"{key}: " for key in keys) + "\n"
