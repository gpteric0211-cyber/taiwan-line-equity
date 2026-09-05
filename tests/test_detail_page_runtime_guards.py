from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DetailPageRuntimeGuardTests(unittest.TestCase):
    def test_cost_renderer_uses_defined_raw_text_guard(self) -> None:
        page = (ROOT / "review_src" / "static" / "detail.html").read_text(encoding="utf-8")

        self.assertIn("function rawDisplayLeak", page)
        self.assertIn("rawDisplayLeak(s)", page)
        self.assertNotIn("badText(s)", page)
        self.assertIn("分價量資料暫不可用", page)

    def test_official_kline_renderer_and_exact_value_table_are_wired(self) -> None:
        page = (ROOT / "review_src" / "static" / "detail.html").read_text(encoding="utf-8")

        self.assertIn("function renderKline", page)
        self.assertIn("renderKline(d.kline_history||{})", page)
        self.assertIn("歷史日 K 與成交量", page)
        self.assertIn("成交量（股）", page)
        self.assertIn("原始值，未推估", page)
        self.assertNotIn("source_label||", page)
        self.assertNotIn("probability_up", page)

    def test_dashboard_does_not_render_database_path_or_provider_configuration(self) -> None:
        page = (ROOT / "review_src" / "static" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("d.db_path", page)
        self.assertNotIn("d.fugle_enabled", page)
        self.assertNotIn("d.finmind_enabled", page)


if __name__ == "__main__":
    unittest.main()
