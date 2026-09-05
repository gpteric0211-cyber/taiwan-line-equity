from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from price_volume import analyze_volume_structure  # noqa: E402


class PriceVolumeStructureTest(unittest.TestCase):
    def test_empty_profile_is_insufficient_without_high_low_fallback(self) -> None:
        result = analyze_volume_structure([], 280)
        self.assertEqual(result["source_status"], "insufficient_volume_profile")
        self.assertIsNone(result["support_zone"])
        self.assertIsNone(result["pressure_zone"])
        self.assertIsNone(result["poc_price"])

    def test_poc_tie_uses_nearest_reference_price(self) -> None:
        result = analyze_volume_structure(
            [
                {"price": 279.0, "volume": 100},
                {"price": 281.0, "volume": 100},
                {"price": 286.0, "volume": 10},
            ],
            280.8,
        )
        self.assertEqual(result["source_status"], "ok")
        self.assertEqual(result["poc_price"], 281.0)

    def test_support_and_pressure_clusters_from_high_volume_rows(self) -> None:
        result = analyze_volume_structure(
            [
                {"price": 268.0, "volume": 10},
                {"price": 269.0, "volume": 10},
                {"price": 270.0, "volume": 10},
                {"price": 271.5, "volume": 90},
                {"price": 272.0, "volume": 90},
                {"price": 280.0, "volume": 10},
                {"price": 288.0, "volume": 10},
                {"price": 289.0, "volume": 10},
                {"price": 290.0, "volume": 95},
                {"price": 290.5, "volume": 95},
                {"price": 292.0, "volume": 10},
            ],
            280.0,
        )
        self.assertEqual(result["source_status"], "ok")
        self.assertEqual(result["support_zone"]["low"], 271.5)
        self.assertEqual(result["support_zone"]["high"], 272.0)
        self.assertEqual(result["pressure_zone"]["low"], 290.0)
        self.assertEqual(result["pressure_zone"]["high"], 290.5)


if __name__ == "__main__":
    unittest.main()
