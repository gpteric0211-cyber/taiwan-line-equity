from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
DEFAULT_DB_PATH = REVIEW_SRC / "data" / "taiwan50.db"
DEFAULT_STATE_PATH = (
    ROOT / "logs" / "market_foundation" / "canonical_surface_parity_state.json"
)
DEFAULT_REPORT_PATH = (
    ROOT / "logs" / "market_foundation" / "canonical_surface_parity_latest.json"
)
BOUNDARY_CODES = ("2455", "1538", "3008", "3661", "2317")
EXIT_SUCCESS = 0
EXIT_CLI_ERROR = 1
EXIT_FATAL = 2
EXIT_SAFETY_MISMATCH = 3

if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.canonical_surface_parity_service import (  # noqa: E402
    DEFAULT_TARGET_BATCHES,
    MONITOR_CONTRACT_VERSION,
    compare_surface_payloads,
    update_monitor_state,
)


def _now_text() -> str:
    return datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")


def _read_only_connection(database: Path) -> sqlite3.Connection:
    target = database.expanduser().resolve()
    conn = sqlite3.connect(
        f"{target.as_uri()}?mode=ro",
        uri=True,
        check_same_thread=False,
        timeout=5,
    )
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(False)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA trusted_schema=OFF")
    return conn


def _database_signature(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "data_version": int(conn.execute("PRAGMA data_version").fetchone()[0]),
        "schema_version": int(conn.execute("PRAGMA schema_version").fetchone()[0]),
        "page_count": int(conn.execute("PRAGMA page_count").fetchone()[0]),
        "freelist_count": int(conn.execute("PRAGMA freelist_count").fetchone()[0]),
        "total_changes": int(conn.total_changes),
    }


def _is_target_database(value: Any, target: Path) -> bool:
    text = str(value or "").strip()
    if not text or text == ":memory:":
        return False
    if text.startswith("file:"):
        uri_base = text.split("?", 1)[0].replace("file:", "file:///", 1)
        return uri_base.rstrip("/").casefold() == target.as_uri().rstrip("/").casefold()
    try:
        return Path(text).expanduser().resolve() == target
    except (OSError, ValueError):
        return False


@contextmanager
def _force_target_database_read_only(database: Path):
    """Make every SQLite connection in this audit process read-only for the live DB."""

    target = database.expanduser().resolve()
    original_connect = sqlite3.connect

    def guarded_connect(database_value: Any, *args: Any, **kwargs: Any):
        if not _is_target_database(database_value, target):
            return original_connect(database_value, *args, **kwargs)
        text = str(database_value)
        if text.startswith("file:") and "mode=ro" in text:
            return original_connect(database_value, *args, **kwargs)
        safe_kwargs = dict(kwargs)
        safe_kwargs["uri"] = True
        return original_connect(
            f"{target.as_uri()}?mode=ro",
            *args,
            **safe_kwargs,
        )

    sqlite3.connect = guarded_connect
    try:
        yield
    finally:
        sqlite3.connect = original_connect


def _read_publications(database: Path) -> list[dict[str, Any]]:
    with _read_only_connection(database) as conn:
        exists = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='full_market_batch_publications'
            """
        ).fetchone()
        if not exists:
            return []
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT trade_date,run_id,published_at,contract_version,
                       expected_active_asof,classified_ohlcv_count,
                       official_no_trade_count,not_applicable_count,
                       expected_universe_hash,classified_universe_hash
                FROM full_market_batch_publications
                ORDER BY trade_date
                """
            ).fetchall()
        ]


def _read_watchlist_codes(database: Path) -> list[str]:
    with _read_only_connection(database) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='watchlist'"
        ).fetchone()
        if not table:
            return []
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(watchlist)").fetchall()
        }
        order_column = "sort_order" if "sort_order" in columns else "code"
        return [
            str(row[0]).strip().zfill(4)
            for row in conn.execute(
                f"SELECT code FROM watchlist ORDER BY {order_column},code"
            ).fetchall()
            if str(row[0] or "").strip()
        ]


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"monitor state is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("monitor state must be a JSON object")
    return value


def _write_json(path: Path, payload: dict[str, Any], *, backup: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        backup_path = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup_path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _normalize_codes(values: list[str]) -> list[str]:
    codes: set[str] = set()
    for value in values:
        for item in str(value or "").split(","):
            code = item.strip()
            if not code:
                continue
            if not (len(code) == 4 and code.isdigit()):
                raise ValueError(f"invalid stock code: {code}")
            codes.add(code)
    return sorted(codes)


def _runtime_modules(database: Path):
    os.environ["TAIWAN50_DB_PATH"] = str(database.expanduser().resolve())
    import app
    from api.bot_market_data import api_bot_daily_market_data
    from services.line_bot_service import _compact_daily

    # The Web projection must be incapable of writing even if a future helper
    # accidentally attempts a mutation.  Canonical repositories already use
    # mode=ro/query_only connections.
    app.db = lambda: _read_only_connection(database)
    return app, api_bot_daily_market_data, _compact_daily


def _default_codes(app_module: Any, database: Path) -> list[str]:
    component_codes = [
        str(item.get("code") or "").strip().zfill(4)
        for item in app_module.read_components()
        if str(item.get("code") or "").strip()
    ]
    return _normalize_codes(
        [*component_codes, *_read_watchlist_codes(database), *BOUNDARY_CODES]
    )


def _safe_exception(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def run_audit(
    *,
    database: Path,
    state_path: Path,
    report_path: Path,
    expected_trade_date: str | None,
    requested_codes: list[str] | None,
    target_batches: int,
    runtime_loader: Callable[[Path], tuple[Any, Any, Any]] = _runtime_modules,
) -> tuple[int, dict[str, Any]]:
    generated_at = _now_text()
    publications = _read_publications(database)
    publication_trade_date = (
        str(publications[-1].get("trade_date")) if publications else None
    )
    target_trade_date = expected_trade_date or publication_trade_date
    if not target_trade_date or publication_trade_date != target_trade_date:
        report = {
            "contract_version": MONITOR_CONTRACT_VERSION,
            "generated_at": generated_at,
            "target_trade_date": target_trade_date,
            "publication_trade_date": publication_trade_date,
            "status": "failed",
            "reason": "latest publication marker does not match the requested batch",
            "target_consecutive_batches": int(target_batches),
            "database_writes": False,
            "source_database_changed_concurrently": False,
            "mismatch_count": 1,
            "mismatches": [
                {
                    "code": None,
                    "surface": "publication_marker",
                    "field": "trade_date",
                    "expected": target_trade_date,
                    "actual": publication_trade_date,
                }
            ],
        }
        _write_json(report_path, report)
        return EXIT_SAFETY_MISMATCH, report

    observer = _read_only_connection(database)
    try:
        before_signature = _database_signature(observer)
        with _force_target_database_read_only(database):
            app_module, bot_builder, line_compactor = runtime_loader(database)
            codes = _normalize_codes(requested_codes or []) if requested_codes else _default_codes(
                app_module,
                database,
            )
            if not codes:
                raise ValueError("no stock codes are available for the parity audit")

            per_code: list[dict[str, Any]] = []
            all_mismatches: list[dict[str, Any]] = []
            database_change_events: list[dict[str, Any]] = []
            previous_signature = before_signature
            for code in codes:
                try:
                    bot_payload = bot_builder(
                        code,
                        trade_date=target_trade_date,
                        include_levels=False,
                        level_limit=25,
                        analysis_mode="close_batch",
                    )
                    stock = dict(bot_payload.get("stock") or {})
                    web_payload = app_module._build_row_uncached(
                        {"code": code, "name": str(stock.get("name") or code)},
                        "tw50",
                        persist_state=False,
                        source_type="taiwan50_batch",
                    )
                    line_payload = line_compactor(bot_payload)
                    result = compare_surface_payloads(
                        code=code,
                        expected_trade_date=target_trade_date,
                        bot_payload=bot_payload,
                        web_payload=web_payload,
                        line_payload=line_payload,
                    )
                except Exception as exc:
                    mismatch = {
                        "code": code,
                        "surface": "runtime",
                        "field": "exception",
                        "expected": "successful read-only projection",
                        "actual": _safe_exception(exc),
                    }
                    result = {
                        "code": code,
                        "passed": False,
                        "analysis_status": None,
                        "main_status": None,
                        "reason_code": None,
                        "rsi14": None,
                        "line_rsi14": None,
                        "cost_contract_version": None,
                        "cost_formula_versions": [],
                        "mismatches": [mismatch],
                    }
                per_code.append(result)
                all_mismatches.extend(result.get("mismatches") or [])

                current_signature = _database_signature(observer)
                if current_signature != previous_signature:
                    database_change_events.append(
                        {
                            "after_code": code,
                            "before": previous_signature,
                            "after": current_signature,
                        }
                    )
                previous_signature = current_signature

        after_signature = _database_signature(observer)
    finally:
        observer.close()

    database_changed = before_signature != after_signature
    passed_codes = sum(1 for item in per_code if item.get("passed"))
    audit_passed = not all_mismatches and passed_codes == len(codes)
    publication = dict(publications[-1])
    observation = {
        "trade_date": target_trade_date,
        "status": "passed" if audit_passed else "failed",
        "publication": publication,
        "scope": {
            "mode": "close_batch",
            "codes": codes,
            "code_count": len(codes),
        },
        "codes_requested": len(codes),
        "codes_checked": len(per_code),
        "passed_codes": passed_codes,
        "failed_codes": len(codes) - passed_codes,
        "mismatch_count": len(all_mismatches),
        "mismatches": all_mismatches[:200],
        "per_code": [
            {key: value for key, value in item.items() if key != "mismatches"}
            for item in per_code
        ],
        "database_writes": False,
        "source_database_changed_concurrently": database_changed,
        "database_write_access_forced_read_only": True,
        "database_change_events": database_change_events,
    }
    state = update_monitor_state(
        _load_state(state_path),
        observation,
        publication_dates=[str(item.get("trade_date")) for item in publications],
        target_batches=target_batches,
        generated_at=generated_at,
    )
    _write_json(state_path, state, backup=True)
    summary = dict(state.get("summary") or {})
    report = {
        "contract_version": MONITOR_CONTRACT_VERSION,
        "generated_at": generated_at,
        "target_trade_date": target_trade_date,
        "publication_trade_date": publication_trade_date,
        "status": observation["status"],
        "target_consecutive_batches": int(target_batches),
        "observed_distinct_trade_dates": summary.get(
            "observed_distinct_trade_dates"
        ),
        "consecutive_pass_count": summary.get("consecutive_pass_count"),
        "remaining_consecutive_batches": summary.get(
            "remaining_consecutive_batches"
        ),
        "stable": summary.get("stable"),
        "monitor_status": summary.get("status"),
        "missing_publication_audits": summary.get("missing_dates") or [],
        "codes_requested": len(codes),
        "codes_checked": len(per_code),
        "passed_codes": passed_codes,
        "failed_codes": len(codes) - passed_codes,
        "mismatch_count": len(all_mismatches),
        "mismatches": all_mismatches[:200],
        "per_code": observation["per_code"],
        "publication": publication,
        "database_writes": False,
        "source_database_changed_concurrently": database_changed,
        "database_write_access_forced_read_only": True,
        "database_change_events": database_change_events,
        "state_path": str(state_path.relative_to(ROOT)) if state_path.is_relative_to(ROOT) else str(state_path),
    }
    _write_json(report_path, report)
    return (EXIT_SUCCESS if audit_passed else EXIT_SAFETY_MISMATCH), report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit canonical close-batch parity across Web, Bot, and LINE."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--expected-trade-date")
    parser.add_argument("--codes", action="append", default=[])
    parser.add_argument(
        "--target-batches",
        type=int,
        default=DEFAULT_TARGET_BATCHES,
    )
    return parser.parse_args(argv)


def _console_summary(report: dict[str, Any], exit_code: int) -> dict[str, Any]:
    return {
        "exit_code": exit_code,
        "status": report.get("status"),
        "trade_date": report.get("publication_trade_date"),
        "codes_checked": report.get("codes_checked", 0),
        "passed_codes": report.get("passed_codes", 0),
        "mismatch_count": report.get("mismatch_count", 0),
        "consecutive_pass_count": report.get("consecutive_pass_count", 0),
        "target_consecutive_batches": report.get("target_consecutive_batches"),
        "stable": bool(report.get("stable")),
        "database_writes": report.get("database_writes", False),
        "source_database_changed_concurrently": report.get(
            "source_database_changed_concurrently",
            False,
        ),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.target_batches < 1:
            raise ValueError("target-batches must be at least 1")
        codes = _normalize_codes(args.codes) if args.codes else None
        exit_code, report = run_audit(
            database=args.db_path,
            state_path=args.state_path,
            report_path=args.report_path,
            expected_trade_date=args.expected_trade_date,
            requested_codes=codes,
            target_batches=args.target_batches,
        )
    except ValueError as exc:
        exit_code = EXIT_CLI_ERROR
        report = {
            "status": "error",
            "reason": _safe_exception(exc),
            "target_consecutive_batches": args.target_batches,
        }
    except Exception as exc:
        exit_code = EXIT_FATAL
        report = {
            "status": "fatal",
            "reason": _safe_exception(exc),
            "target_consecutive_batches": args.target_batches,
        }
    print(
        json.dumps(
            _console_summary(report, exit_code),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
