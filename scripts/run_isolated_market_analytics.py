from __future__ import annotations

"""Publish technical snapshots/vectors only after isolated integrity checks."""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_isolated_post_close_pipeline import run_isolated_update  # noqa: E402


PIPELINE = ROOT / "scripts" / "update_market_analytics_snapshots.py"
REPORT = ROOT / "logs" / "market_foundation" / "isolated_market_analytics_latest.json"


def main() -> int:
    return run_isolated_update(
        list(sys.argv[1:]),
        pipeline=PIPELINE,
        report_path=REPORT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
