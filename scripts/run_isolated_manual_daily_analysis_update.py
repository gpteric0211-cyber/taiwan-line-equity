from __future__ import annotations

"""Run the manual daily analysis update on a snapshot and publish atomically."""

import os
import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_isolated_post_close_pipeline import (  # noqa: E402
    DEFAULT_ACTIVE_DB,
    DEFAULT_LOCK,
    run_isolated_update,
)


MANUAL_PIPELINE = ROOT / "scripts" / "run_manual_daily_analysis_update.py"
ISOLATION_REPORT = ROOT / "logs" / "manual_daily_update" / "isolated_update_latest.json"


def main(argv: list[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    if any(arg in arguments for arg in ("--plan-only", "--help", "-h")):
        return int(
            subprocess.run(
                [sys.executable, str(MANUAL_PIPELINE), *arguments],
                cwd=ROOT,
            ).returncode
        )
    # Freeze the target once: a run crossing midnight must verify its own date.
    from scripts.verify_daily_analysis_update import main as verify_main
    from scripts.verify_daily_analysis_update import recent_market_date_for_post_close

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--date", default=recent_market_date_for_post_close())
    known, _ = parser.parse_known_args(arguments)
    if not any(arg == "--date" or arg.startswith("--date=") for arg in arguments):
        arguments.extend(["--date", known.date])
    configured = os.environ.get("TAIWAN50_DB_PATH", "").strip()
    active = Path(configured).expanduser() if configured else DEFAULT_ACTIVE_DB
    code = run_isolated_update(
        arguments,
        active=active,
        pipeline=MANUAL_PIPELINE,
        lock_path=DEFAULT_LOCK,
        report_path=ISOLATION_REPORT,
    )
    if code:
        return code
    return verify_main(["--database", str(active), "--date", known.date])


if __name__ == "__main__":
    raise SystemExit(main())
