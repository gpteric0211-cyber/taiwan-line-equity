from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.mis import fetch_mis_quotes_batch, get_mis_quote_cached, get_mis_quote_latest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug TWSE MIS quote high/low source for a single code.")
    parser.add_argument("--code", default="3491")
    args = parser.parse_args()
    code = str(args.code or "").strip().zfill(4)
    before = get_mis_quote_cached(code) or get_mis_quote_latest(code)
    fetched = fetch_mis_quotes_batch([code], persist=False, update_cache=False)
    quote = fetched.get(code) or get_mis_quote_cached(code) or get_mis_quote_latest(code)
    result = {
        "ok": bool(quote),
        "code": code,
        "writes_db": False,
        "writes_db_note": "This CLI calls fetch_mis_quotes_batch(persist=False, update_cache=False), so it does not persist mis_quote_snapshot or mutate the process cache.",
        "before_cached_quote": before,
        "quote": quote,
        "raw_high_low_proven": bool(quote and quote.get("high") is not None and quote.get("low") is not None),
        "today_support_source": "MIS raw field l -> parsed quote.low" if quote and quote.get("low") is not None else "unproven",
        "today_resistance_source": "MIS raw field h -> parsed quote.high" if quote and quote.get("high") is not None else "unproven",
        "fallback_warning": "Current build_row uses MIS quote.low/high only when quote is fresh; otherwise intraday today support/resistance displays dash.",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if quote else 1


if __name__ == "__main__":
    raise SystemExit(main())
