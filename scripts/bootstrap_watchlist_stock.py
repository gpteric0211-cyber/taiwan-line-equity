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
    and not os.environ.get("WATCHLIST_BOOTSTRAP_SCRIPT_REEXEC")
    and VENV_PYTHON.exists()
    and Path(sys.executable).resolve() != VENV_PYTHON.resolve()
):
    env = os.environ.copy()
    env["WATCHLIST_BOOTSTRAP_SCRIPT_REEXEC"] = "1"
    raise SystemExit(subprocess.call([str(VENV_PYTHON), *sys.argv], env=env))

if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

import app as _app  # noqa: F401,E402  # Configures app-level bootstrap callbacks for this manual script.
from core.components import read_components  # noqa: E402
from core.db import init_db  # noqa: E402
from repository.watchlist_repository import get_watchlist_codes  # noqa: E402
from services.watchlist_bootstrap_service import bootstrap_watchlist_codes  # noqa: E402


def _clean_code(value: Any) -> str | None:
    code = str(value or "").strip().zfill(4)[:4]
    return code if code.isdigit() and len(code) == 4 else None


def _codes_from_args(args: argparse.Namespace) -> list[str]:
    if args.codes:
        values = [_clean_code(x) for x in str(args.codes).split(",")]
        return [x for x in values if x]
    if args.mode == "tw50":
        return [str(item.get("code") or "").zfill(4)[:4] for item in read_components()]
    if args.mode == "watchlist":
        return get_watchlist_codes()
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap local detail data for newly added watchlist stocks.")
    parser.add_argument("--codes", default="", help="Comma-separated stock codes, for example 1101,2303.")
    parser.add_argument("--mode", choices=["watchlist", "tw50"], default="watchlist")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true", help="Run one bounded pass. Kept for scheduler compatibility.")
    parser.add_argument("--skip-tdcc", action="store_true")
    parser.add_argument("--skip-daily-chip", action="store_true")
    args = parser.parse_args()

    init_db()
    codes = _codes_from_args(args)
    if not codes:
        message = "watchlist is empty or unavailable" if args.mode == "watchlist" and not args.codes else "no valid stock codes"
        print(json.dumps({
            "ok": False,
            "mode": args.mode,
            "dry_run": bool(args.dry_run),
            "writes_db": False,
            "message": message,
            "warnings": [message],
            "total": 0,
            "codes": [],
            "error": message,
        }, ensure_ascii=False, indent=2))
        return 1
    result = bootstrap_watchlist_codes(
        codes,
        days=int(args.days),
        mode=args.mode,
        run_tdcc=not bool(args.skip_tdcc),
        run_daily_chip=not bool(args.skip_daily_chip),
        dry_run=bool(args.dry_run),
    )
    result["mode"] = args.mode
    result["dry_run"] = bool(args.dry_run)
    result["writes_db"] = bool(
        not args.dry_run
        and any(item.get("actions") for item in result.get("per_code", result.get("results", [])))
    )
    result.setdefault("success", sum(1 for item in result.get("per_code", result.get("results", [])) if item.get("ok")))
    result.setdefault("failed_count", int(result.get("total") or 0) - int(result.get("success") or 0))
    result.setdefault("codes", [str(item.get("code") or "") for item in result.get("per_code", result.get("results", [])) if item.get("code")])
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
