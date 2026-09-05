from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.yahoo_timesales import build_price_volume_distribution_from_time_sales


def main() -> int:
    rows = [
        {"price": "100", "volume_lots": "10", "side": "BUY"},
        {"price": 100, "volume_lots": 5, "side": "SELL"},
        {"price": 100, "volume_lots": 7, "side": ""},
        {"price": 100, "volume_lots": 3},
        {"price": "101.5", "volume_lots": "2", "side": "UNKNOWN"},
        {"price": "bad", "volume_lots": "99", "side": "BUY"},
    ]
    grouped = build_price_volume_distribution_from_time_sales(
        rows,
        code="2330",
        trade_date="2026-06-23",
        fetched_at=123.0,
    )
    by_price = {item["price"]: item for item in grouped}
    assert by_price[100.0]["volume_lots"] == 25
    assert by_price[100.0]["buy_volume_lots"] == 10
    assert by_price[100.0]["sell_volume_lots"] == 5
    assert by_price[100.0]["neutral_volume_lots"] == 10
    assert by_price[101.5]["neutral_volume_lots"] == 2
    print(json.dumps({"ok": True, "rows": grouped}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
