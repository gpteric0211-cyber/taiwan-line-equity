from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
VENV_PYTHON = REVIEW_SRC / ".venv" / "Scripts" / "python.exe"
if (
    os.name == "nt"
    and not os.environ.get("TDCC_EQUITY_SCRIPT_REEXEC")
    and VENV_PYTHON.exists()
    and Path(sys.executable).resolve() != VENV_PYTHON.resolve()
):
    env = os.environ.copy()
    env["TDCC_EQUITY_SCRIPT_REEXEC"] = "1"
    raise SystemExit(subprocess.call([str(VENV_PYTHON), *sys.argv], env=env))

if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.components import read_components
from core.db import db, init_db
from repository.watchlist_repository import get_watchlist_codes
from repository.market_analytics_repository import active_stock_codes
from services.tdcc_equity_concentration_service import (
    normalize_tdcc_code,
    update_tdcc_equity_concentration_for_codes,
)
try:  # Support both ``python scripts/x.py`` and ``import scripts.x``.
    from scripts.run_isolated_post_close_pipeline import (  # type: ignore
        DEFAULT_LOCK as MARKET_DATABASE_UPDATE_LOCK,
        isolated_update_lock,
    )
except ModuleNotFoundError:
    from run_isolated_post_close_pipeline import (  # type: ignore
        DEFAULT_LOCK as MARKET_DATABASE_UPDATE_LOCK,
        isolated_update_lock,
    )


def _json_default(value: Any) -> str:
    return str(value)


def _dedupe(codes: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in codes:
        code = normalize_tdcc_code(value)
        if code and code not in seen:
            out.append(code)
            seen.add(code)
    return out


def _codes_for_args(args: argparse.Namespace) -> list[str]:
    if args.code:
        code = normalize_tdcc_code(args.code)
        return [code] if code else []
    if args.mode == "watchlist":
        return _dedupe(get_watchlist_codes())
    if args.mode == "tw50":
        return _dedupe([row["code"] for row in read_components()])
    if args.mode == "all":
        with db() as conn:
            return _dedupe(active_stock_codes(conn))
    return []


def _exit_code(result: dict[str, Any]) -> int:
    total = int(result.get("total_codes") or 0)
    success = int(result.get("success") or 0)
    if total > 0 and success == total:
        return 0
    if success == 0:
        return 1
    return 2


def _run(args: argparse.Namespace) -> int:
    if not args.dry_run:
        init_db()
    codes = _codes_for_args(args)
    if args.limit is not None:
        codes = codes[: max(0, args.limit)]
    if not codes:
        print(json.dumps({
            "ok": False,
            "mode": args.mode,
            "code": args.code,
            "source": args.source,
            "dry_run": bool(args.dry_run),
            "error": "no valid stock codes",
        }, ensure_ascii=False, indent=2))
        return 1

    result = update_tdcc_equity_concentration_for_codes(
        codes,
        source=args.source,
        days=args.days,
        dry_run=bool(args.dry_run),
        limit=args.limit,
    )
    output: dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "mode": "code" if args.code else args.mode,
        "source": result.get("source"),
        "dry_run": bool(result.get("dry_run")),
        "total_codes": result.get("total_codes"),
        "success": result.get("success"),
        "missing": result.get("missing"),
        "parse_error": result.get("parse_error"),
        "stale": result.get("stale"),
        "distribution_rows": result.get("distribution_rows"),
        "summaries": result.get("summaries"),
        "latest_date": result.get("latest_date"),
        "writes_db": not bool(result.get("dry_run")),
        "codes": result.get("codes"),
    }
    if args.verbose:
        output["errors"] = result.get("errors") or []
        output["source_debug"] = result.get("source_debug") or {}
    else:
        output["error_count"] = len(result.get("errors") or [])

    print(json.dumps(output, ensure_ascii=False, indent=2, default=_json_default))
    return _exit_code(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Update weekly TDCC equity concentration summaries for manual runs or Windows Task Scheduler."
    )
    parser.add_argument("--code", help="Single 4-digit stock code, for example 2317.")
    parser.add_argument("--mode", choices=["watchlist", "tw50", "all"], default="tw50")
    parser.add_argument("--source", choices=["tdcc", "finmind", "auto"], default="tdcc")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse only. Do not write SQLite.")
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of codes processed.")
    parser.add_argument("--verbose", action="store_true", help="Print source debug and per-code errors.")
    parser.add_argument("--lock-wait-seconds", type=float, default=3600)
    args = parser.parse_args(argv)

    if args.dry_run:
        return _run(args)
    with isolated_update_lock(
        MARKET_DATABASE_UPDATE_LOCK,
        wait_seconds=max(0.0, args.lock_wait_seconds),
    ) as acquired:
        if not acquired:
            print(json.dumps({"ok": False, "status": "MARKET_DATABASE_UPDATE_LOCK_BUSY"}))
            return 4
        return _run(args)


if __name__ == "__main__":
    from core.tls_config import configure_tls
    configure_tls()
    raise SystemExit(main())
