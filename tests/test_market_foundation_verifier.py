from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_market_foundation_update import (  # noqa: E402
    _latest_official_source_date,
    _official_rows_by_source,
    _official_source_kind,
)


def test_exact_date_official_sources_are_recognized() -> None:
    assert _official_source_kind("TWSE MI_INDEX") == "twse"
    assert _official_source_kind("TPEX DAILY_QUOTES") == "tpex"
    counts = [
        {"source": "TWSE MI_INDEX", "latest_date": "2026-08-24", "row_count": 1084},
        {"source": "TPEX DAILY_QUOTES", "latest_date": "2026-08-24", "row_count": 860},
    ]
    assert _latest_official_source_date(counts) == "2026-08-24"
    assert _official_rows_by_source(counts, "2026-08-24") == (1084, 860)
