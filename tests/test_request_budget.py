from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.twse_institution import fetch_twse_institution_dry_run  # noqa: E402
from core.request_budget import RequestBudget, RequestCapExceeded, budgeted_get_json  # noqa: E402


class FakeResponse:
    status_code = 200
    encoding = "utf-8"
    url = "https://fake.local/endpoint?response=json"
    text = '{"fields":[],"data":[]}'

    def json(self):
        return {"fields": ["code", "name"], "data": [["2330", "台積電", "", "", "100", "", "", "", "", "", "20", "5"]], "date": "20260701"}


class RequestBudgetTests(unittest.TestCase):
    def test_consume_stops_before_next_request(self):
        budget = RequestBudget(max_requests=1)
        budget.consume("https://fake.local/one", purpose="first")
        with self.assertRaises(RequestCapExceeded):
            budget.consume("https://fake.local/two", purpose="second")
        self.assertEqual(budget.request_count, 1)
        self.assertTrue(budget.request_cap_exceeded)

    def test_retry_counts_against_same_budget_and_blocks_second_call(self):
        calls: list[str] = []

        def fake_get(url, **kwargs):
            calls.append(url)
            raise RuntimeError("forced failure")

        budget = RequestBudget(max_requests=1)
        with self.assertRaises(RequestCapExceeded):
            budgeted_get_json(
                "https://fake.local/retry",
                budget=budget,
                retries=2,
                retry_wait=0,
                http_get=fake_get,
            )
        self.assertEqual(calls, ["https://fake.local/retry"])
        self.assertEqual(budget.request_count, 1)
        self.assertTrue(budget.request_cap_exceeded)

    def test_dry_run_output_includes_request_accounting(self):
        def fake_get(url, **kwargs):
            return FakeResponse()

        result = fetch_twse_institution_dry_run("2026-07-01", max_requests=1, http_get=fake_get)
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(result["max_requests"], 1)
        self.assertFalse(result["request_cap_exceeded"])
        self.assertFalse(result["writes_db"])


if __name__ == "__main__":
    unittest.main()
