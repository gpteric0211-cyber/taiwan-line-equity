from __future__ import annotations

import math
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from analysis.screening_prefilter import screening_prefilter_score  # noqa: E402


def test_bottom_prefilter_preserves_the_existing_formula() -> None:
    row = {
        "close": 100,
        "ma20": 98,
        "ma60": 95,
        "rsi14": 40,
        "previous_rsi14": 38,
        "macd_osc": -0.2,
        "previous_macd_osc": -0.5,
        "volume": 1_200_000,
        "volume_ma20": 1_000_000,
        "turnover_value": 120_000_000,
    }
    expected = (
        3 * math.log10(120_000_000)
        - 6 * 2
        - 1.5 * 2
        + 3 * 2
        + 0.8 * 0.3
    )

    assert screening_prefilter_score(row, "bottom") == expected


def test_prefilter_score_is_not_a_final_verdict() -> None:
    row = {"close": 100, "ma20": 100, "ma60": 100, "rsi14": 38, "turnover_value": 1e9}

    result = screening_prefilter_score(row, "bottom")

    assert isinstance(result, float)
