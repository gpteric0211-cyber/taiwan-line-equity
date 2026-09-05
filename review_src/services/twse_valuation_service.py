from __future__ import annotations

import logging
import time
from typing import Any

from adapter.twse_valuation import TwseValuationSourceDelayed, fetch_twse_bwibbu_day
from repository.twse_valuation_repository import (
    cleanup_twse_daily_valuation,
    upsert_twse_daily_valuations,
)


logger = logging.getLogger(__name__)


def update_twse_daily_valuation(
    data_date: str | None = None,
    *,
    retention_days: int = 200,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fetch official TWSE BWIBBU valuation rows and persist them to SQLite."""
    started = time.perf_counter()
    try:
        rows = fetch_twse_bwibbu_day(data_date)
        if not rows:
            raise RuntimeError("TWSE BWIBBU returned no rows")
        effective_date = max(str(row["data_date"]) for row in rows if row.get("data_date"))
        if dry_run:
            upsert_count = 0
            cleanup = {"dry_run": True}
        else:
            upsert_count = upsert_twse_daily_valuations(rows)
            cleanup = cleanup_twse_daily_valuation(retention_days=retention_days)
        result = {
            "ok": True,
            "status": "ok",
            "source": "TWSE_BWIBBU",
            "data_date": effective_date,
            "requested_date": data_date,
            "row_count": len(rows),
            "upsert_count": upsert_count,
            "rows_written": upsert_count,
            "writes_db": bool(not dry_run and upsert_count > 0),
            "retryable": False,
            "retention_days": retention_days,
            "dry_run": dry_run,
            "cleanup": cleanup,
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        logger.info(
            "TWSE valuation update ok data_date=%s rows=%s dry_run=%s duration=%s",
            result["data_date"],
            result["row_count"],
            dry_run,
            result["duration_seconds"],
        )
        return result
    except TwseValuationSourceDelayed as exc:
        logger.warning(
            "TWSE valuation source delayed requested_date=%s available_date=%s",
            exc.requested_date,
            exc.available_date,
        )
        return {
            "ok": False,
            "status": "source_delayed",
            "source": "TWSE_BWIBBU",
            "data_date": None,
            "requested_date": exc.requested_date,
            "available_date": exc.available_date,
            "available_dates": exc.available_dates,
            "row_count": 0,
            "upsert_count": 0,
            "rows_written": 0,
            "writes_db": False,
            "retryable": True,
            "retention_days": retention_days,
            "dry_run": dry_run,
            "error_reason": str(exc),
            "source_errors": exc.source_errors,
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
    except Exception as exc:
        logger.exception("TWSE valuation update failed data_date=%s", data_date)
        return {
            "ok": False,
            "status": "failed",
            "source": "TWSE_BWIBBU",
            "data_date": data_date,
            "requested_date": data_date,
            "row_count": 0,
            "upsert_count": 0,
            "rows_written": 0,
            "writes_db": False,
            "retryable": False,
            "retention_days": retention_days,
            "dry_run": dry_run,
            "error_reason": str(exc),
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
