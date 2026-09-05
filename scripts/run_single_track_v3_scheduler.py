from __future__ import annotations

"""Run or inspect the durable Single-Track V3 scheduler outside request paths."""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = PROJECT_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.single_track_v3_schema import ensure_single_track_v3_schema  # noqa: E402
from repository.single_track_v3_calendar_repository import (  # noqa: E402
    seal_calendar_revision,
)
from task.single_track_v3_scheduler import (  # noqa: E402
    process_zero_gpu_artifacts,
    run_scheduler_tick,
    scheduler_preflight,
)


TPE = ZoneInfo("Asia/Taipei")
DEFAULT_DATABASE = REVIEW_SRC / "data" / "taiwan50.db"
DEFAULT_SOURCE_POLICY_VERSION = "SourceAuthorityPolicyV1"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _observed_at(value: str | None) -> str:
    if value is None:
        return datetime.now(TPE).isoformat(timespec="seconds")
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("--observed-at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--observed-at must include an explicit UTC offset")
    return parsed.astimezone(TPE).isoformat(timespec="seconds")


def _database_path(argument: Path | None) -> Path:
    configured = argument
    if configured is None:
        configured_text = os.environ.get("TAIWAN50_DB_PATH", "").strip()
        configured = Path(configured_text) if configured_text else DEFAULT_DATABASE
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()


def _read_only_connection(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise FileNotFoundError("configured scheduler database does not exist")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _writable_connection(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise FileNotFoundError("configured scheduler database does not exist")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _offline_probe_calendar(conn: sqlite3.Connection) -> None:
    created_at = "2026-08-01T09:05:00+08:00"
    seal_calendar_revision(
        conn,
        {
            "calendar_revision": "offline-probe-calendar-r1",
            "source_id": "offline_fixture_not_production_evidence",
            "source_url": "https://example.invalid/offline-probe",
            "source_digest": _sha("offline-probe-source"),
            "session_policy_version": "OfflineProbeSessionPolicyV1",
            "revision_published_at": "2026-08-01T09:00:00+08:00",
            "revision_available_at": "2026-08-01T09:01:00+08:00",
            "timezone": "Asia/Taipei",
            "sealed_at": created_at,
            "created_at": created_at,
        },
        [
            {
                "trade_date": trade_date,
                "session_state": state,
                "scheduled_open_at": (
                    f"{trade_date}T{opened}:00+08:00" if opened else None
                ),
                "scheduled_close_at": (
                    f"{trade_date}T{closed}:00+08:00" if closed else None
                ),
                "cancellation_reason": cancellation_reason,
                "source_evidence_digest": _sha(
                    f"offline-probe:{trade_date}:{state}"
                ),
                "created_at": created_at,
            }
            for trade_date, state, opened, closed, cancellation_reason in (
                ("2026-09-04", "scheduled", "09:00", "13:30", None),
                ("2026-09-05", "cancelled", None, None, "offline_probe_cancelled"),
                ("2026-09-07", "scheduled", "09:00", "13:30", None),
                ("2026-09-08", "delayed", "10:00", "13:30", None),
                ("2026-09-09", "special_session", "11:00", "12:00", None),
            )
        ],
    )


def _temporary_restart_probe() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="single-track-v3-scheduler-") as directory:
        database = Path(directory) / "scheduler-probe.sqlite3"
        first_conn = _writable_connection_for_probe(database)
        ensure_single_track_v3_schema(first_conn)
        _offline_probe_calendar(first_conn)
        first = run_scheduler_tick(
            first_conn,
            observed_at="2026-09-06T12:07:00+08:00",
            source_policy_version=DEFAULT_SOURCE_POLICY_VERSION,
        )
        first_conn.commit()
        first_count = int(
            first_conn.execute("SELECT COUNT(*) FROM news_retrieval_run").fetchone()[0]
        )
        first_conn.close()

        reopened = _writable_connection_for_probe(database)
        replay = run_scheduler_tick(
            reopened,
            observed_at="2026-09-06T12:07:00+08:00",
            source_policy_version=DEFAULT_SOURCE_POLICY_VERSION,
        )
        replay_count = int(
            reopened.execute("SELECT COUNT(*) FROM news_retrieval_run").fetchone()[0]
        )
        advanced = run_scheduler_tick(
            reopened,
            observed_at="2026-09-06T12:16:00+08:00",
            source_policy_version=DEFAULT_SOURCE_POLICY_VERSION,
        )
        reopened.commit()
        advanced_count = int(
            reopened.execute("SELECT COUNT(*) FROM news_retrieval_run").fetchone()[0]
        )
        reopened.close()
    passed = (
        first.get("replayed") is False
        and replay.get("replayed") is True
        and first_count == replay_count
        and len(advanced.get("materialized_run_ids") or []) == 1
        and advanced_count == first_count + 1
    )
    return {
        "contract_version": "SingleTrackV3SchedulerOfflineRestartProbeV1",
        "status": "pass" if passed else "fail",
        "passed": passed,
        "synthetic_offline_probe": True,
        "production_database_used": False,
        "production_scheduler_installed": False,
        "first_tick_materialized": len(first.get("materialized_run_ids") or []),
        "first_tick_skipped": len(first.get("skipped_run_ids") or []),
        "replay_detected": replay.get("replayed") is True,
        "run_count_after_first": first_count,
        "run_count_after_replay": replay_count,
        "advanced_tick_materialized": len(
            advanced.get("materialized_run_ids") or []
        ),
        "run_count_after_advanced_tick": advanced_count,
        "zero_gpu_model_calls": 0,
    }


def _writable_connection_for_probe(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preflight or run the durable Single-Track V3 scheduler."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--temporary-restart-probe", action="store_true")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--observed-at")
    parser.add_argument(
        "--source-policy-version",
        default=DEFAULT_SOURCE_POLICY_VERSION,
    )
    parser.add_argument("--zero-gpu-limit", type=int, default=100)
    parser.add_argument("--allow-write", action="store_true")
    args = parser.parse_args()

    if args.temporary_restart_probe:
        print(_json(_temporary_restart_probe()))
        return 0

    observed_at = _observed_at(args.observed_at)
    database = _database_path(args.database)
    if not args.run:
        conn = _read_only_connection(database)
        try:
            result = scheduler_preflight(conn, observed_at=observed_at)
        finally:
            conn.close()
        print(_json(result))
        return 0

    if not args.allow_write:
        raise SystemExit("--run requires explicit --allow-write")
    if args.zero_gpu_limit < 1 or args.zero_gpu_limit > 1000:
        raise SystemExit("--zero-gpu-limit must be between 1 and 1000")
    conn = _writable_connection(database)
    try:
        preflight = scheduler_preflight(conn, observed_at=observed_at)
        if not preflight["schema_ready"] or not preflight["calendar_ready"]:
            raise RuntimeError(
                "scheduler write refused: schema/calendar preflight is not ready"
            )
        tick = run_scheduler_tick(
            conn,
            observed_at=observed_at,
            source_policy_version=args.source_policy_version,
        )
        zero_gpu = process_zero_gpu_artifacts(
            conn,
            observed_at=observed_at,
            limit=args.zero_gpu_limit,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(
        _json(
            {
                "contract_version": "SingleTrackV3SchedulerRunResultV1",
                "observed_at": observed_at,
                "scheduler_tick": tick,
                "zero_gpu_artifacts": zero_gpu,
                "retrieval_worker_executed": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
