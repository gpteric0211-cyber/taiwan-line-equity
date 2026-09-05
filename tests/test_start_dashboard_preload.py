from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("root_start_dashboard", ROOT / "start_dashboard.py")
assert SPEC is not None and SPEC.loader is not None
START_DASHBOARD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = START_DASHBOARD
SPEC.loader.exec_module(START_DASHBOARD)
dashboard_payload_ready = START_DASHBOARD.dashboard_payload_ready


class DashboardPreloadTests(unittest.TestCase):
    def test_ready_requires_every_checked_row(self) -> None:
        payload = {
            "ready": True,
            "rows": [{"code": "1101"}, {"code": "1216"}],
            "readiness": {"ready": True, "checked": 2, "pass_count": 2, "fail_count": 0},
        }
        self.assertTrue(dashboard_payload_ready(payload))

    def test_partial_payload_is_not_ready(self) -> None:
        payload = {
            "ready": False,
            "rows": [],
            "readiness": {"ready": False, "checked": 50, "pass_count": 2, "fail_count": 48},
        }
        self.assertFalse(dashboard_payload_ready(payload))


if __name__ == "__main__":
    unittest.main()
