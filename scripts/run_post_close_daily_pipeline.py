from __future__ import annotations

import json
import argparse
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
REPORT_PATH = ROOT / "docs" / "POST_CLOSE_DAILY_PIPELINE_REPORT.json"
SURFACE_PARITY_REPORT_PATH = ROOT / "logs" / "market_foundation" / "canonical_surface_parity_latest.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.config import DB_PATH  # noqa: E402
from core.db import assert_db_integrity  # noqa: E402
from core.market_session import recent_market_date_for_post_close  # noqa: E402
from core.utils import now_tpe  # noqa: E402
from services.price_volume_daily_service import (  # noqa: E402
    reconcile_full_market_price_volume_after_official_update,
    recover_missing_price_volume_from_persisted_trades,
)
from services.stock_master_service import sync_official_stock_master  # noqa: E402
from services.target_outcome_materializer import (  # noqa: E402
    materialize_available_target_outcomes,
)


LOCK_PATH = ROOT / "logs" / "market_foundation" / "post_close_daily_pipeline.lock"
OFFICIAL_REPORT_PATH = ROOT / "docs" / "ALL_MARKET_DATABASE_UPDATE_REPORT.json"
RETRYABLE_CODES = {4, 5}
DATABASE_INTEGRITY_FAILURE_EXIT_CODE = 10


def target_trade_date() -> str:
    """Freeze the trading date using the shared 15:00 post-close calendar rule."""

    return recent_market_date_for_post_close()


def absolute_window_deadline(reference: datetime, window_end: str) -> datetime:
    """Resolve one invocation's wall-clock deadline without midnight rollover."""

    end_hour, end_minute = (int(part) for part in window_end.split(":", 1))
    return reference.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)


def write_progress_report(payload: dict) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_suffix(REPORT_PATH.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(REPORT_PATH)


def run_surface_parity_audit(trade_date: str) -> dict[str, object]:
    """Run the non-mutating cross-surface contract audit for one published batch."""

    command = [
        sys.executable,
        str(ROOT / "scripts" / "audit_canonical_surface_parity.py"),
        "--expected-trade-date",
        trade_date,
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    exit_code = int(completed.returncode)
    summary: dict[str, object] = {}
    for line in reversed(str(getattr(completed, "stdout", "") or "").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            summary = value
            break
    return {
        **summary,
        "exit_code": exit_code,
        "status": summary.get("status") or ("passed" if exit_code == 0 else "failed"),
        "report_path": str(SURFACE_PARITY_REPORT_PATH.relative_to(ROOT)),
        "retryable": False,
    }


@contextmanager
def exclusive_process_lock():
    """Prevent overlapping manual, legacy, and Task Scheduler executions."""

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    acquired = False
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                acquired = False
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                acquired = False
        yield acquired
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def capture_progress(trade_date: str) -> dict[str, int]:
    if not DB_PATH.exists():
        return {
            "active_stocks": 0,
            "no_trade_stocks": 0,
            "required_capture_stocks": 0,
            "attempted_stocks": 0,
            "distribution_stocks": 0,
            "validated_distribution_stocks": 0,
            "scoped_validated_distribution_stocks": 0,
        }
    conn = sqlite3.connect(f"{DB_PATH.resolve().as_uri()}?mode=ro", uri=True)
    try:
        active = conn.execute(
            "SELECT COUNT(*) FROM stock_master WHERE is_active=1 AND security_type='stock'"
        ).fetchone()[0]
        no_trade = conn.execute(
            """
            SELECT COUNT(DISTINCT n.code)
            FROM stock_no_trade_dates n
            JOIN stock_master s ON s.code=n.code
            WHERE n.trade_date=? AND s.is_active=1 AND s.security_type='stock'
            """,
            (trade_date,),
        ).fetchone()[0]
        attempted = conn.execute(
            """
            SELECT COUNT(DISTINCT r.code)
            FROM fugle_intraday_capture_runs r
            JOIN stock_master s ON s.code=r.code
            WHERE r.trade_date=? AND r.endpoint='trades'
              AND s.is_active=1 AND s.security_type='stock'
              AND NOT EXISTS (
                SELECT 1 FROM stock_no_trade_dates n
                WHERE n.code=r.code AND n.trade_date=r.trade_date
              )
            """,
            (trade_date,),
        ).fetchone()[0]
        distribution = conn.execute(
            "SELECT COUNT(DISTINCT stock_id) FROM price_volume_distribution WHERE trade_date=?",
            (trade_date,),
        ).fetchone()[0]
        validated = conn.execute(
            """
            SELECT COUNT(DISTINCT stock_id) FROM price_volume_distribution
            WHERE trade_date=? AND UPPER(COALESCE(data_quality,''))='VALIDATED'
            """,
            (trade_date,),
        ).fetchone()[0]
        scoped_validated = conn.execute(
            """
            SELECT COUNT(DISTINCT stock_id) FROM price_volume_distribution
            WHERE trade_date=? AND UPPER(COALESCE(data_quality,''))='SCOPED_VALIDATED'
            """,
            (trade_date,),
        ).fetchone()[0]
        return {
            "active_stocks": int(active or 0),
            "no_trade_stocks": int(no_trade or 0),
            "required_capture_stocks": max(int(active or 0) - int(no_trade or 0), 0),
            "attempted_stocks": int(attempted or 0),
            "distribution_stocks": int(distribution or 0),
            "validated_distribution_stocks": int(validated or 0),
            "scoped_validated_distribution_stocks": int(scoped_validated or 0),
        }
    finally:
        conn.close()


def capture_needed_codes(trade_date: str) -> list[str]:
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(f"{DB_PATH.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return [
            str(row[0])
            for row in conn.execute(
                """
                SELECT s.code
                FROM stock_master s
                WHERE s.is_active=1 AND s.security_type='stock'
                  AND NOT EXISTS (
                    SELECT 1 FROM stock_no_trade_dates n
                    WHERE n.code=s.code AND n.trade_date=?
                  )
                  AND (
                    NOT EXISTS (
                      SELECT 1 FROM price_volume_distribution p
                      WHERE p.stock_id=s.code AND p.trade_date=?
                    )
                    OR (
                      NOT EXISTS (
                        SELECT 1 FROM price_volume_distribution p
                        WHERE p.stock_id=s.code AND p.trade_date=?
                          AND UPPER(COALESCE(p.data_quality,'')) IN (
                              'VALIDATED','SCOPED_VALIDATED'
                          )
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM fugle_intraday_capture_runs r
                        WHERE r.code=s.code AND r.trade_date=? AND r.endpoint='trades'
                          AND (
                            (
                              r.capture_complete=1
                              AND UPPER(COALESCE(r.data_quality,''))='SESSION_COMPLETE'
                            )
                            OR (
                              UPPER(COALESCE(r.data_quality,''))=
                                'PAGINATION_COMPLETE_SESSION_UNVERIFIED'
                              AND COALESCE(r.normalized_row_count,0)>0
                              AND r.stored_row_count=r.normalized_row_count
                            )
                          )
                      )
                    )
                  )
                ORDER BY s.code
                """,
                (trade_date, trade_date, trade_date, trade_date),
            ).fetchall()
        ]
    finally:
        conn.close()


def record_unavailable_capture_attempts(trade_date: str, codes: list[str]) -> int:
    if not codes:
        return 0
    conn = sqlite3.connect(DB_PATH)
    try:
        now_text = datetime.now().astimezone().isoformat(timespec="seconds")
        fetched_at = time.time()
        conn.executemany(
            """
            INSERT INTO fugle_intraday_capture_runs(
                code,trade_date,endpoint,source,snapshot_time,page_count,
                provider_row_count,normalized_row_count,stored_row_count,
                capture_complete,data_quality,reason,latest_trade_time,
                latest_cumulative_volume,captured_volume_lots,fetched_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(code,trade_date,endpoint,source) DO UPDATE SET
                snapshot_time=excluded.snapshot_time,
                capture_complete=0,
                data_quality=excluded.data_quality,
                reason=excluded.reason,
                fetched_at=excluded.fetched_at
            """,
            [
                (
                    code,
                    trade_date,
                    "trades",
                    "FUGLE",
                    now_text,
                    0,
                    0,
                    0,
                    0,
                    0,
                    "UNAVAILABLE",
                    "provider returned no accepted same-date trades after the bounded retry policy",
                    "",
                    None,
                    None,
                    fetched_at,
                )
                for code in codes
            ],
        )
        conn.commit()
        return len(codes)
    finally:
        conn.close()


def _load_official_report() -> dict:
    if not OFFICIAL_REPORT_PATH.exists():
        return {}
    try:
        value = json.loads(OFFICIAL_REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _verified_official_trade_date(result: dict, requested_date: str) -> str | None:
    exact = result.get("official_exact_date_ohlcv") or {}
    exact_date = str(exact.get("trade_date") or "")
    if bool(exact.get("ok")) and bool(exact.get("storage_allowed")) and exact_date == requested_date:
        return requested_date
    return None


def archive_shadow_outcomes_after_official_update(
    *,
    enabled: bool,
    official_update_exit_code: int,
    verified_official_trade_date: str | None,
) -> dict[str, object]:
    """Archive eligible T+1 labels only behind an explicit shadow opt-in.

    This source-level hook is intentionally disabled by default. It neither
    installs nor changes a scheduler, and its result cannot alter the Stable
    post-close exit code.
    """

    if not enabled:
        return {
            "status": "disabled_pending_rollout_authorization",
            "enabled": False,
            "writes_attempted": False,
            "blocking_stable_pipeline": False,
        }
    if official_update_exit_code != 0 or verified_official_trade_date is None:
        return {
            "status": "awaiting_verified_official_close",
            "enabled": True,
            "writes_attempted": False,
            "blocking_stable_pipeline": False,
        }
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        outcome = materialize_available_target_outcomes(
            conn,
            recorded_at=now_tpe().isoformat(timespec="seconds"),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return {
            "status": "shadow_archive_failed",
            "enabled": True,
            "writes_attempted": True,
            "blocking_stable_pipeline": False,
            "error": str(exc),
        }
    finally:
        conn.close()
    return {
        "status": "complete",
        "enabled": True,
        "writes_attempted": True,
        "blocking_stable_pipeline": False,
        **outcome,
    }


def _capture_stage(
    trade_date: str,
    started_at: str,
    *,
    window_end: str = "23:59",
) -> dict:
    """Capture Fugle rows only; never promote, reconcile, or score them."""

    try:
        stock_master = sync_official_stock_master(dry_run=False)
    except Exception as exc:
        # Continue from the last persisted official universe, but keep the
        # stage retryable.  A failed metadata refresh must not suppress the
        # licensed same-day capture window.
        stock_master = {
            "ok": False,
            "status": "failed",
            "writes_db": False,
            "error": str(exc),
            "universe_status": "provisional_from_last_official_sync",
        }
    trade_date = trade_date or target_trade_date()
    before = capture_progress(trade_date)
    write_progress_report(
        {
            "status": "running",
            "stage": "fugle_capture",
            "trade_date": trade_date,
            "started_at": started_at,
            "stock_master": stock_master,
            "capture_before": before,
            "pipeline_order": "stock_master_then_fugle_then_later_official_verification",
            "official_update_deferred": True,
        }
    )
    recovery_before = recover_missing_price_volume_from_persisted_trades(trade_date)
    requested_codes = capture_needed_codes(trade_date)
    capture_needed = bool(requested_codes)
    capture_exit_codes: list[int] = []
    capture_phase = "post_close" if trade_date == now_tpe().date().isoformat() else "latest_completed"
    write_progress_report(
        {
            "status": "running",
            "stage": "capture",
            "trade_date": trade_date,
            "started_at": started_at,
            "stock_master": stock_master,
            "capture_before": before,
            "pipeline_order": "stock_master_then_fugle_then_later_official_verification",
            "official_update_deferred": True,
            "persisted_trade_recovery_before": recovery_before,
            "capture_requested_count": len(requested_codes),
        }
    )
    if capture_needed:
        # One updater process owns the whole same-day request budget.  Splitting
        # the universe across child processes would reset the rolling limiter
        # and could exceed the provider's requests-per-minute contract.
        capture_command = [
            sys.executable,
            str(ROOT / "run_fugle_all_from_xlsx_progress.py"),
            "--capture-phase",
            capture_phase,
            "--window-start",
            "00:00" if capture_phase == "latest_completed" else "13:31",
            "--window-end",
            window_end,
            "--batch-size",
            str(len(requested_codes)),
            "--max-retries",
            "0",
            "--skip-final-verify",
            "--codes",
            ",".join(requested_codes),
        ]
        capture_exit_codes.append(int(subprocess.run(capture_command, cwd=ROOT).returncode))
        write_progress_report(
            {
                "status": "running",
                "stage": "capture",
                "trade_date": trade_date,
                "started_at": started_at,
                "stock_master": stock_master,
                "pipeline_order": "stock_master_then_fugle_then_later_official_verification",
                "official_update_deferred": True,
                "capture_requested_count": len(requested_codes),
                "capture_process_count": 1,
                "capture_runner_exit_codes": list(capture_exit_codes),
                "capture_progress": capture_progress(trade_date),
            }
        )
    recovery_after = recover_missing_price_volume_from_persisted_trades(trade_date)
    after = capture_progress(trade_date)
    remaining_capture_codes = capture_needed_codes(trade_date)
    capture_operational_complete = bool(
        after["required_capture_stocks"] > 0
        and after["attempted_stocks"] >= after["required_capture_stocks"]
        and not remaining_capture_codes
    )
    nonretryable_codes = [code for code in capture_exit_codes if code not in {0, *RETRYABLE_CODES}]
    source_delayed_failure = 5 in capture_exit_codes
    partial_retryable_failure = 4 in capture_exit_codes
    capture_exit_code = (
        int(nonretryable_codes[0])
        if nonretryable_codes
        else 5
        if source_delayed_failure
        else 4
        if partial_retryable_failure or not capture_operational_complete
        else 4
        if not stock_master.get("ok")
        else 0
    )
    result = {
        "status": (
            "capture_complete_awaiting_official_verification" if capture_exit_code == 0 else "capture_partial"
        ),
        "stage": "fugle_capture",
        "trade_date": trade_date,
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "stock_master": stock_master,
        "pipeline_order": "stock_master_then_fugle_then_later_official_verification",
        "official_update_deferred": True,
        "capture_before": before,
        "persisted_trade_recovery_before": recovery_before,
        "capture_requested_count": len(requested_codes),
        "capture_runner_exit_codes": capture_exit_codes,
        "persisted_trade_recovery_after": recovery_after,
        "remaining_capture_count": len(remaining_capture_codes),
        "remaining_capture_examples": remaining_capture_codes[:25],
        "capture_after": after,
        "capture_operational_complete": capture_operational_complete,
        "official_verification_status": "pending",
        "price_volume_capture_ready": False,
        "price_volume_is_decision_ready": False,
        "note": (
            "Fugle rows are persisted but remain excluded from scoring until "
            "the later official exact-date volume reconciliation succeeds."
        ),
    }
    write_progress_report(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"exit_code": capture_exit_code, "result": result}


def run_capture_pipeline(
    trade_date: str | None = None,
    *,
    window_end: str = "23:59",
) -> int:
    frozen_date = trade_date or target_trade_date()
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    if frozen_date != now_tpe().date().isoformat():
        result = {
            "status": "historical_capture_not_attempted",
            "stage": "fugle_capture",
            "trade_date": frozen_date,
            "started_at": started_at,
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "fugle_called": False,
            "official_update_deferred": True,
            "price_volume_capture_ready": False,
            "price_volume_is_decision_ready": False,
            "reason": (
                "Fugle intraday trades cannot be assumed reproducible on T+1; "
                "the official catch-up task will run without historical recapture."
            ),
        }
        write_progress_report(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return int(
        _capture_stage(
            frozen_date,
            started_at,
            window_end=window_end,
        )["exit_code"]
    )


def run_finalize_pipeline(
    trade_date: str | None = None,
    *,
    enable_shadow_outcome_archive: bool = False,
    publish_official_core: bool = False,
) -> int:
    """Run official EOD update and reconciliation without calling Fugle."""

    trade_date = trade_date or target_trade_date()
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    before = capture_progress(trade_date)
    write_progress_report(
        {
            "status": "running",
            "stage": "official_verification",
            "trade_date": trade_date,
            "started_at": started_at,
            "capture_before": before,
            "pipeline_order": "fugle_capture_then_official_verification",
        }
    )
    official = [
        sys.executable,
        str(ROOT / "scripts" / "update_all_market_database.py"),
        "--date",
        trade_date,
        "--report",
        str(OFFICIAL_REPORT_PATH),
    ]
    official_code = int(subprocess.run(official, cwd=ROOT).returncode)
    official_result = _load_official_report()
    verified_trade_date = _verified_official_trade_date(official_result, trade_date)
    shadow_outcome_archive = archive_shadow_outcomes_after_official_update(
        enabled=enable_shadow_outcome_archive,
        official_update_exit_code=official_code,
        verified_official_trade_date=verified_trade_date,
    )
    recovery_after = (
        recover_missing_price_volume_from_persisted_trades(
            trade_date,
            verified_official_trade_date=verified_trade_date,
        )
        if verified_trade_date
        else {
            "ok": False,
            "status": "source_delayed",
            "target_date": trade_date,
            "recovered_count": 0,
        }
    )
    price_volume_reconciliation = reconcile_full_market_price_volume_after_official_update(
        {
            "verified_trade_date": verified_trade_date,
            "official_sources": [],
        }
    )
    remaining_capture_codes = capture_needed_codes(trade_date)
    after = capture_progress(trade_date)
    persisted_capture_validated_count = (
        after["validated_distribution_stocks"] + after["scoped_validated_distribution_stocks"]
    )
    required_scoring_count = int(price_volume_reconciliation.get("required_trading_stock_count") or 0)
    reconciled_capture_validated_count = int(
        price_volume_reconciliation.get("capture_validated_count")
        or (
            int(price_volume_reconciliation.get("validated_count") or 0)
            + int(price_volume_reconciliation.get("scoped_validated_count") or 0)
        )
        or persisted_capture_validated_count
    )
    reconciliation_blocking_count = sum(
        int(price_volume_reconciliation.get(key) or 0)
        for key in ("missing_capture_count", "retryable_count", "rejected_count")
    )
    price_volume_capture_ready = bool(
        required_scoring_count > 0
        and reconciled_capture_validated_count >= required_scoring_count
        and reconciliation_blocking_count == 0
        and price_volume_reconciliation.get("ok")
    )
    price_volume_is_decision_ready = bool(
        price_volume_capture_ready
        and required_scoring_count > 0
        and int(price_volume_reconciliation.get("decision_ready_count") or 0) >= required_scoring_count
    )
    parity_skip_reason = (
        f"official update exit code {official_code}; all required official components must be publishable"
        if official_code != 0
        else "supplemental price-volume capture has not passed official reconciliation"
    )
    surface_parity_audit: dict[str, object] = {
        "status": "skipped",
        "reason": parity_skip_reason,
        "exit_code": None,
        "retryable": False,
    }
    if official_code == 0 and price_volume_capture_ready:
        surface_parity_audit = run_surface_parity_audit(trade_date)
    surface_parity_failed = bool(surface_parity_audit.get("exit_code") not in {None, 0})
    result = {
        "trade_date": trade_date,
        "status": (
            "complete"
            if official_code == 0 and price_volume_is_decision_ready and not surface_parity_failed
            else (
                "surface_parity_failed"
                if official_code == 0 and price_volume_capture_ready and surface_parity_failed
                else "data_collection_complete_analysis_history_pending"
                if official_code == 0 and price_volume_capture_ready
                else "official_complete_supplemental_pending"
                if official_code == 0
                else "partial"
            )
        ),
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "stage": "official_verification",
        "pipeline_order": "fugle_capture_then_official_verification",
        "fugle_called_during_finalize": False,
        "capture_requested_count": 0,
        "capture_runner_exit_codes": [],
        "persisted_trade_recovery_after": recovery_after,
        "remaining_capture_count": len(remaining_capture_codes),
        "remaining_capture_examples": remaining_capture_codes[:25],
        "official_unobserved_codes": list(price_volume_reconciliation.get("official_unobserved_codes") or []),
        "reconciliation_blocking_count": reconciliation_blocking_count,
        "reconciled_capture_validated_count": reconciled_capture_validated_count,
        "official_core_ready": official_code == 0 and verified_trade_date == trade_date,
        "publication_scope": "official_core" if publish_official_core else "full_capture",
        "full_analysis_ready": price_volume_is_decision_ready
        and not surface_parity_failed
        and official_code == 0,
        "official_update_exit_code": official_code,
        "official_verified_trade_date": verified_trade_date,
        "shadow_outcome_archive": shadow_outcome_archive,
        "price_volume_reconciliation": price_volume_reconciliation,
        "capture_before": before,
        "capture_after": after,
        "price_volume_capture_attempted_for_full_universe": bool(
            after["required_capture_stocks"] > 0
            and after["attempted_stocks"] >= after["required_capture_stocks"]
        ),
        "price_volume_capture_ready": price_volume_capture_ready,
        "price_volume_is_decision_ready": price_volume_is_decision_ready,
        "surface_parity_audit": surface_parity_audit,
        "note": "Historical Fugle trades are never relabelled as a complete same-day capture when the provider cannot reproduce them.",
    }
    write_progress_report(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if official_code != 0:
        return official_code
    if surface_parity_failed:
        return int(surface_parity_audit.get("exit_code") or 3)
    # Missing historical score coverage cannot be repaired by repeatedly
    # downloading the same trading day. Retry only an incomplete daily capture.
    if publish_official_core and verified_trade_date == trade_date:
        # The unified app can publish verified official data while supplemental
        # capture stays explicitly pending. All analysis readiness flags remain
        # unchanged, and parity / official-source failures above still block.
        return 0
    return 0 if price_volume_capture_ready else 4


def run_with_retries(
    *,
    stage: str,
    max_retries: int,
    retry_delay_seconds: int,
    window_end: str,
    trade_date: str | None = None,
    window_deadline: datetime | None = None,
    enable_shadow_outcome_archive: bool = False,
    publish_official_core: bool = False,
) -> int:
    """Run one frozen trade date and retry only explicit retryable outcomes."""

    try:
        assert_db_integrity(DB_PATH)
    except (RuntimeError, sqlite3.DatabaseError) as exc:
        result = {
            "status": "database_integrity_failed",
            "integrity_phase": "preflight",
            "stage": stage,
            "trade_date": trade_date or target_trade_date(),
            "database": str(DB_PATH),
            "retryable": False,
            "exit_code": DATABASE_INTEGRITY_FAILURE_EXIT_CODE,
            "error": str(exc),
            "reason": "No scheduled update writes were attempted because the database failed its preflight integrity check.",
        }
        write_progress_report(result)
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return DATABASE_INTEGRITY_FAILURE_EXIT_CODE

    invocation_time = now_tpe()
    trade_date = trade_date or target_trade_date()
    catch_up = trade_date < now_tpe().date().isoformat()
    deadline = window_deadline or absolute_window_deadline(
        invocation_time,
        window_end,
    )
    retry_count = 0

    while True:
        if stage == "capture":
            code = run_capture_pipeline(trade_date, window_end=window_end)
        elif publish_official_core:
            code = run_finalize_pipeline(
                trade_date,
                enable_shadow_outcome_archive=enable_shadow_outcome_archive,
                publish_official_core=True,
            )
        elif enable_shadow_outcome_archive:
            code = run_finalize_pipeline(
                trade_date,
                enable_shadow_outcome_archive=True,
            )
        else:
            code = run_finalize_pipeline(trade_date)
        try:
            assert_db_integrity(DB_PATH, full=True)
        except (RuntimeError, sqlite3.DatabaseError) as exc:
            result = {
                "status": "database_integrity_failed",
                "integrity_phase": "postflight",
                "stage": stage,
                "trade_date": trade_date,
                "database": str(DB_PATH),
                "retryable": False,
                "exit_code": DATABASE_INTEGRITY_FAILURE_EXIT_CODE,
                "stage_exit_code": code,
                "error": str(exc),
                "reason": (
                    "The scheduled stage completed, but its post-write database "
                    "integrity check failed. The task is failed closed and must "
                    "not report success."
                ),
            }
            write_progress_report(result)
            print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
            return DATABASE_INTEGRITY_FAILURE_EXIT_CODE
        if code not in RETRYABLE_CODES:
            return code
        if retry_count >= max(0, max_retries):
            return code

        now = now_tpe()
        if catch_up:
            if retry_count >= 2:
                return code
            wait_seconds = min(max(0, retry_delay_seconds), 300)
        else:
            if now >= deadline:
                return code
            wait_seconds = max(0, retry_delay_seconds)
            if now.timestamp() + wait_seconds >= deadline.timestamp():
                return code

        retry_count += 1
        print(
            f"Post-close pipeline remains retryable for {trade_date}; "
            f"retry {retry_count}/{max_retries} in {wait_seconds} seconds.",
            flush=True,
        )
        time.sleep(wait_seconds)


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Run one isolated post-close capture or official-finalization stage."
    )
    parser.add_argument(
        "--stage",
        choices=("capture", "finalize"),
        default="finalize",
        help="capture calls Fugle only; finalize calls official sources only.",
    )
    parser.add_argument(
        "--publish-official-core",
        action="store_true",
        help="Permit verified official-data publication while supplemental capture remains pending; does not enable analysis scoring.",
    )
    parser.add_argument("--max-retries", type=int, default=18)
    parser.add_argument("--retry-delay-seconds", type=int, default=1800)
    parser.add_argument("--window-end", default="23:59")
    parser.add_argument("--lock-wait-seconds", type=int, default=0)
    parser.add_argument("--date", dest="trade_date", help="Frozen target trading date YYYY-MM-DD.")
    parser.add_argument(
        "--enable-shadow-outcome-archive",
        action="store_true",
        help=(
            "Opt in to V11 shadow-only outcome archival after a verified official close; "
            "this does not enable candidate serving or install a scheduler."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    invocation_time = now_tpe()
    frozen_trade_date = args.trade_date or target_trade_date()
    window_deadline = absolute_window_deadline(invocation_time, args.window_end)
    wait_deadline = time.monotonic() + max(0, args.lock_wait_seconds)
    while True:
        if now_tpe() >= window_deadline:
            print("Post-close pipeline window has ended; this stage remains retryable.")
            return 4
        with exclusive_process_lock() as acquired:
            if acquired:
                return run_with_retries(
                    stage=args.stage,
                    max_retries=args.max_retries,
                    retry_delay_seconds=args.retry_delay_seconds,
                    window_end=args.window_end,
                    trade_date=frozen_trade_date,
                    window_deadline=window_deadline,
                    enable_shadow_outcome_archive=args.enable_shadow_outcome_archive,
                    publish_official_core=args.publish_official_core,
                )
        if time.monotonic() >= wait_deadline:
            print("Post-close pipeline lock is busy; this stage remains retryable.")
            return 4
        wall_seconds_remaining = max(
            0,
            int(window_deadline.timestamp() - now_tpe().timestamp()),
        )
        time.sleep(
            min(
                30,
                wall_seconds_remaining,
                max(1, int(wait_deadline - time.monotonic())),
            )
        )


if __name__ == "__main__":
    from core.tls_config import configure_tls

    configure_tls()
    raise SystemExit(main())
