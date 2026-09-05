from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = REPO_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.db import assert_db_integrity, db  # noqa: E402
from core.fugle_intraday_schema import (  # noqa: E402
    ensure_fugle_intraday_schema,
    upsert_fugle_bid_ask_summary,
    upsert_fugle_capture_run,
    upsert_fugle_trade,
)
from services.price_volume_service import reconcile_price_volume_profile_for_code  # noqa: E402
from scripts import update_fugle_intraday_supplemental as fugle  # noqa: E402


DEFAULT_COMMIT_INTERVAL_CODES = 25


def _load_payload(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"cached payload is not a JSON object: {path.name}")
    return payload, hashlib.sha256(raw).hexdigest()


def load_cached_endpoint(
    cache_dir: Path,
    trade_date: str,
    code: str,
    endpoint: str,
) -> dict[str, Any]:
    if endpoint not in {"trades", "volumes"}:
        raise ValueError(f"unsupported cached endpoint: {endpoint}")
    clean_code = str(code).strip().zfill(4)
    path = cache_dir / f"{trade_date}_{clean_code}_{endpoint}.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing cached payload: {path.name}")
    payload, digest = _load_payload(path)
    payload_date = fugle.payload_date(payload)
    payload_symbol = fugle.payload_symbol(payload)
    if payload_date != trade_date:
        raise ValueError(
            f"{path.name} date mismatch: expected {trade_date}, got {payload_date or 'missing'}"
        )
    if payload_symbol != clean_code:
        raise ValueError(
            f"{path.name} symbol mismatch: expected {clean_code}, got {payload_symbol or 'missing'}"
        )
    return {
        "code": clean_code,
        "trade_date": trade_date,
        "endpoint": endpoint,
        "path": path,
        "payload": payload,
        "digest": digest,
        "captured_at": path.stat().st_mtime,
    }


def load_cached_pair(
    cache_dir: Path,
    trade_date: str,
    code: str,
) -> dict[str, Any]:
    clean_code = str(code).strip().zfill(4)
    endpoints = {
        endpoint: load_cached_endpoint(cache_dir, trade_date, clean_code, endpoint)
        for endpoint in ("trades", "volumes")
    }
    paths = {endpoint: value["path"] for endpoint, value in endpoints.items()}
    payloads: dict[str, dict[str, Any]] = {}
    digests: dict[str, str] = {}
    for endpoint, value in endpoints.items():
        payloads[endpoint] = value["payload"]
        digests[endpoint] = value["digest"]
    captured_at = max(path.stat().st_mtime for path in paths.values())
    return {
        "code": clean_code,
        "trade_date": trade_date,
        "paths": paths,
        "payloads": payloads,
        "digests": digests,
        "captured_at": captured_at,
    }


def _active_codes(conn: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute(
            """
            SELECT code FROM stock_master
            WHERE is_active=1 AND security_type='stock'
            ORDER BY market,code
            """
        ).fetchall()
    ]


def replay_cached_payloads(
    cache_dir: Path,
    trade_date: str,
    *,
    codes: list[str] | None = None,
    commit_interval_codes: int = DEFAULT_COMMIT_INTERVAL_CODES,
    reconcile_official: bool = False,
    allow_trade_only_with_validated_distribution: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    cache_dir = cache_dir.resolve()
    if not cache_dir.is_dir():
        raise FileNotFoundError(f"cache directory not found: {cache_dir}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", trade_date):
        raise ValueError("trade_date must be YYYY-MM-DD")
    commit_interval = max(1, int(commit_interval_codes))
    manifest_digest = hashlib.sha256()
    results: list[dict[str, Any]] = []
    written_trade_rows = 0
    capture_rows = 0
    bid_ask_rows = 0
    commit_count = 0

    with closing(db()) as conn:
        if not dry_run:
            ensure_fugle_intraday_schema(conn)
            conn.commit()
        selected_codes = sorted(
            {
                str(code).strip().zfill(4)
                for code in (codes or _active_codes(conn))
                if str(code).strip().isdigit()
            }
        )
        pending_codes = 0
        for code in selected_codes:
            try:
                try:
                    cached = load_cached_pair(cache_dir, trade_date, code)
                    trade_only = False
                except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
                    if not allow_trade_only_with_validated_distribution:
                        raise
                    quality_row = conn.execute(
                        """
                        SELECT COUNT(*) FROM price_volume_distribution
                        WHERE stock_id=? AND trade_date=?
                          AND UPPER(COALESCE(data_quality,source_quality,''))
                              IN ('VALIDATED','SCOPED_VALIDATED')
                        """,
                        (code, trade_date),
                    ).fetchone()
                    if not quality_row or int(quality_row[0] or 0) <= 0:
                        raise RuntimeError(
                            f"{code} cannot use trade-only cache without a validated distribution"
                        )
                    trade_cached = load_cached_endpoint(
                        cache_dir, trade_date, code, "trades"
                    )
                    cached = {
                        "payloads": {"trades": trade_cached["payload"]},
                        "digests": {"trades": trade_cached["digest"]},
                        "captured_at": trade_cached["captured_at"],
                    }
                    trade_only = True
                trade_payload = cached["payloads"]["trades"]
                volume_payload = cached["payloads"].get("volumes")
                fetched_at = float(cached["captured_at"])
                snapshot_time = datetime.fromtimestamp(
                    fetched_at, fugle.TPE
                ).isoformat(timespec="seconds")
                raw_trade_rows = fugle.payload_rows(trade_payload)
                raw_volume_rows = (
                    fugle.payload_rows(volume_payload) if volume_payload else []
                )
                trade_rows = fugle.normalize_trade_rows(
                    code, trade_date, raw_trade_rows, "CACHED_SOURCE_PAYLOAD", fetched_at
                )
                previous_close = fugle.previous_close_for_trade_date(
                    conn, code, trade_date
                )
                trade_rows, _side_stats = fugle.apply_side_inference(
                    trade_rows, previous_close
                )
                volume_rows, bid_ask_summary = fugle.normalize_volume_rows(
                    code,
                    trade_date,
                    raw_volume_rows,
                    "CACHED_SOURCE_PAYLOAD",
                    fetched_at,
                )
                for endpoint in cached["digests"]:
                    manifest_digest.update(
                        f"{trade_date}:{code}:{endpoint}:{cached['digests'][endpoint]}\n".encode(
                            "ascii"
                        )
                    )
                sizes = [fugle.num_int(row.get("size")) for row in trade_rows]
                size_complete = bool(
                    sizes and all(value is not None and value >= 0 for value in sizes)
                )
                captured_volume_lots = (
                    sum(int(value or 0) for value in sizes) if size_complete else None
                )
                latest_trade_time = max(
                    (str(row.get("trade_time") or "") for row in trade_rows),
                    default="",
                )
                stored_rows = 0
                summary_written = 0
                if not dry_run:
                    conn.execute(
                        """
                        DELETE FROM fugle_intraday_trades
                        WHERE code=? AND trade_date=? AND source='FUGLE'
                        """,
                        (code, trade_date),
                    )
                    stored_rows = sum(
                        1
                        for row in trade_rows
                        if upsert_fugle_trade(conn, row, ensure_schema=False)
                    )
                    pagination_candidate = bool(
                        trade_rows
                        and size_complete
                        and stored_rows == len(trade_rows)
                    )
                    upsert_fugle_capture_run(
                        conn,
                        {
                            "code": code,
                            "trade_date": trade_date,
                            "endpoint": "trades",
                            "snapshot_time": snapshot_time,
                            # The combined cache intentionally does not claim the
                            # original provider page count. Official reconciliation
                            # validates the reconstructed terminal evidence instead.
                            "page_count": None,
                            "provider_row_count": len(raw_trade_rows),
                            "normalized_row_count": len(trade_rows),
                            "stored_row_count": stored_rows,
                            "capture_complete": False,
                            "data_quality": (
                                "PAGINATION_COMPLETE_SESSION_UNVERIFIED"
                                if pagination_candidate
                                else "UNAVAILABLE"
                            ),
                            "reason": (
                                "replayed from persisted same-date Fugle API payload cache; "
                                "official reconciliation is required"
                                if pagination_candidate
                                else "cached payload contained no complete normalized trades"
                            ),
                            "latest_trade_time": latest_trade_time,
                            "latest_cumulative_volume": max(
                                (
                                    int(value)
                                    for value in (
                                        fugle.num_int(row.get("volume"))
                                        for row in trade_rows
                                    )
                                    if value is not None
                                ),
                                default=None,
                            ),
                            "captured_volume_lots": captured_volume_lots,
                            "fetched_at": fetched_at,
                        },
                        ensure_schema=False,
                    )
                    if volume_rows:
                        summary_written = int(
                            bool(
                                fugle.upsert_fugle_bid_ask_summary(
                                    conn, bid_ask_summary
                                )
                            )
                        )
                    pending_codes += 1
                    if pending_codes >= commit_interval:
                        conn.commit()
                        commit_count += 1
                        pending_codes = 0
                written_trade_rows += stored_rows
                capture_rows += int(not dry_run)
                bid_ask_rows += summary_written
                results.append(
                    {
                        "code": code,
                        "status": "ok" if trade_rows else "no_trades",
                        "trade_rows": len(trade_rows),
                        "volume_rows": len(volume_rows),
                        "stored_trade_rows": stored_rows,
                        "captured_at": snapshot_time,
                        "trade_only_cache": trade_only,
                    }
                )
            except Exception as exc:
                results.append(
                    {"code": code, "status": "failed", "error": str(exc)}
                )
        if not dry_run and pending_codes:
            conn.commit()
            commit_count += 1

    reconciliation_results: list[dict[str, Any]] = []
    if reconcile_official and not dry_run:
        for row in results:
            if row.get("status") != "ok":
                continue
            result = reconcile_price_volume_profile_for_code(
                str(row["code"]), trade_date
            )
            reconciliation_results.append(
                {
                    "code": row["code"],
                    "status": result.get("status"),
                    "quality": result.get("quality"),
                    "writes_db": result.get("writes_db"),
                }
            )

    failed = [row for row in results if row.get("status") == "failed"]
    no_trades = [row for row in results if row.get("status") == "no_trades"]
    reconciliation_failures = [
        row
        for row in reconciliation_results
        if str(row.get("status") or "").lower()
        not in {"ok", "validated", "scoped_validated"}
        and str(row.get("quality") or "").upper()
        not in {"VALIDATED", "SCOPED_VALIDATED"}
    ]
    return {
        "ok": not failed and not reconciliation_failures,
        "status": (
            "ok"
            if not failed and not reconciliation_failures
            else "partial"
        ),
        "dry_run": dry_run,
        "trade_date": trade_date,
        "cache_dir": str(cache_dir),
        "selected_code_count": len(results),
        "successful_trade_code_count": len(results) - len(failed) - len(no_trades),
        "no_trade_code_count": len(no_trades),
        "failed_code_count": len(failed),
        "failed_codes": [row["code"] for row in failed],
        "failed_results": failed,
        "written_trade_rows": written_trade_rows,
        "capture_rows_written": capture_rows,
        "bid_ask_rows_written": bid_ask_rows,
        "commit_interval_codes": commit_interval,
        "commit_count": commit_count,
        "cache_manifest_digest": manifest_digest.hexdigest(),
        "reconcile_official": reconcile_official,
        "allow_trade_only_with_validated_distribution": (
            allow_trade_only_with_validated_distribution
        ),
        "reconciliation_count": len(reconciliation_results),
        "reconciliation_failure_count": len(reconciliation_failures),
        "reconciliation_failures": reconciliation_failures[:100],
        "result_sample": results[:50],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay persisted same-date Fugle API JSON payloads after database recovery."
        )
    )
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--codes")
    parser.add_argument(
        "--commit-interval-codes", type=int, default=DEFAULT_COMMIT_INTERVAL_CODES
    )
    parser.add_argument("--reconcile-official", action="store_true")
    parser.add_argument(
        "--allow-trade-only-with-validated-distribution",
        action="store_true",
        help=(
            "Use an exact cached trades payload when the volumes cache is unreadable, "
            "but only if the destination already has a validated distribution."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    codes = fugle.normalize_codes(args.codes) if args.codes else None
    result = replay_cached_payloads(
        args.cache_dir,
        args.date,
        codes=codes,
        commit_interval_codes=args.commit_interval_codes,
        reconcile_official=args.reconcile_official,
        allow_trade_only_with_validated_distribution=(
            args.allow_trade_only_with_validated_distribution
        ),
        dry_run=args.dry_run,
    )
    if not args.dry_run:
        assert_db_integrity()
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        report = args.report.resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
