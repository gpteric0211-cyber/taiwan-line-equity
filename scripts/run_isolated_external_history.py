from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_isolated_post_close_pipeline import run_isolated_update  # noqa: E402


def main() -> int:
    return run_isolated_update(
        list(sys.argv[1:]),
        pipeline=ROOT / "scripts" / "materialize_external_history_candidate.py",
        report_path=ROOT / "logs" / "market_foundation" / "isolated_external_history_latest.json",
    )


if __name__ == "__main__":
    raise SystemExit(main())
