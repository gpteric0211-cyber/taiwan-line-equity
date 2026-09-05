from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.twse_valuation import TwseValuationSourceDelayed  # noqa: E402
from services.twse_valuation_service import update_twse_daily_valuation  # noqa: E402


def test_source_delay_is_structured_and_never_writes_stale_rows() -> None:
    delayed = TwseValuationSourceDelayed(
        "2026-08-28",
        available_dates={"2026-08-27"},
        source_errors=["RWD: no matching data", "OpenAPI: returned 2026-08-27"],
    )
    with patch(
        "services.twse_valuation_service.fetch_twse_bwibbu_day",
        side_effect=delayed,
    ), patch(
        "services.twse_valuation_service.upsert_twse_daily_valuations"
    ) as upsert:
        result = update_twse_daily_valuation("2026-08-28")

    assert result["ok"] is False
    assert result["status"] == "source_delayed"
    assert result["requested_date"] == "2026-08-28"
    assert result["available_date"] == "2026-08-27"
    assert result["data_date"] is None
    assert result["rows_written"] == 0
    assert result["writes_db"] is False
    assert result["retryable"] is True
    upsert.assert_not_called()


def test_success_reports_exact_effective_date_and_write_count() -> None:
    rows = [{"data_date": "2026-08-28", "symbol": "2330"}]
    with patch(
        "services.twse_valuation_service.fetch_twse_bwibbu_day",
        return_value=rows,
    ), patch(
        "services.twse_valuation_service.upsert_twse_daily_valuations",
        return_value=1,
    ), patch(
        "services.twse_valuation_service.cleanup_twse_daily_valuation",
        return_value={"deleted": 0},
    ):
        result = update_twse_daily_valuation("2026-08-28")

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["data_date"] == "2026-08-28"
    assert result["rows_written"] == 1
    assert result["writes_db"] is True
    assert result["retryable"] is False
