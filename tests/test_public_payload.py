from __future__ import annotations

import sys
import json
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.public_payload import sanitize_public_market_payload  # noqa: E402
from api.config import api_config  # noqa: E402


def test_public_projection_removes_nested_provenance_but_keeps_market_facts() -> None:
    internal = {
        "code": "2330",
        "stock": {
            "market": "listed",
            "exchange": "TWSE",
            "source": "official master",
            "yahoo_symbol": "2330.TW",
            "fugle_market": "TWSE",
        },
        "ohlcv": {
            "close": 100,
            "source": "provider endpoint",
            "source_quality": "official",
        },
        "data_sources": [{"source_name": "internal provider"}],
        "kline": {"rows": [1], "source_label": "internal provider"},
        "quality": {"distribution_source_quality": "validated", "status": "ok"},
        "costs": {"estimate": {"value": 98, "source_meta": {"source": "internal"}}},
    }

    public = sanitize_public_market_payload(internal)

    assert public["code"] == "2330"
    assert public["stock"] == {"market": "listed", "exchange": "TWSE"}
    assert public["ohlcv"] == {"close": 100}
    assert "data_sources" not in public
    assert public["kline"] == {"rows": [1]}
    assert public["quality"] == {"status": "ok"}
    assert public["costs"] == {"estimate": {"value": 98}}
    assert internal["ohlcv"]["source"] == "provider endpoint"


def test_public_projection_masks_local_absolute_paths() -> None:
    public = sanitize_public_market_payload(
        {"message": r"read C:\Users\Example\project\data.sqlite3 failed"}
    )

    assert "C:\\Users" not in public["message"]
    assert "本機路徑已隱藏" in public["message"]


def test_public_config_does_not_expose_provider_credentials_paths_or_errors() -> None:
    payload = api_config()
    encoded = json.dumps(payload, ensure_ascii=False).lower()

    for forbidden_key in (
        "db_path",
        "key_hint",
        "token_disabled_reason",
        "truststore_error",
        "mis_source",
    ):
        assert forbidden_key not in payload
    for provider_name in ("finmind", "fugle", "yahoo", "pchome", "getstockinfo.jsp"):
        assert provider_name not in encoded
