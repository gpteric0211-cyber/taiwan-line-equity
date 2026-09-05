from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from core.config import (
    AUTO_UPDATE_TW50_ON_START,
    EOD_PAGE_REFRESH_SECONDS,
    FUGLE_AUTO_UPDATE_MARKET_HOURS,
    MIS_AUTO_UPDATE_MARKET_HOURS,
    MIS_CACHE_TTL_SECONDS,
    MIS_POLL_SECONDS,
)
from core.market_session import tw_market_session_now


router = APIRouter()
_truststore_enabled = False
_truststore_error = ""


def configure_config_router(*, truststore_enabled: bool, truststore_error: str) -> None:
    global _truststore_enabled, _truststore_error
    _truststore_enabled = truststore_enabled
    _truststore_error = truststore_error


@router.get("/api/config")
def api_config() -> dict[str, Any]:
    """Return only the normal frontend runtime contract.

    Provider identity, token hints, local paths and dependency errors remain
    available to backend diagnostics and logs, not to the public dashboard.
    """

    return {
        "realtime_quotes_enabled": MIS_AUTO_UPDATE_MARKET_HOURS,
        "quote_poll_seconds": MIS_POLL_SECONDS,
        "quote_cache_ttl_seconds": MIS_CACHE_TTL_SECONDS,
        "eod_refresh_seconds": EOD_PAGE_REFRESH_SECONDS,
        "supplemental_realtime_enabled": FUGLE_AUTO_UPDATE_MARKET_HOURS,
        "auto_update_tw50_on_start": AUTO_UPDATE_TW50_ON_START,
        "tw_market_session": tw_market_session_now(),
        "persistent_storage": True,
        "update_notes": {
            "daily_close": "收盤資料於交易日盤後更新；未通過完整性檢查時不顯示推測值。",
            "institution": "法人資料盤後陸續更新，實際可用時間以後端首次驗證通過為準。",
            "margin_lending": "融資融券／借券資料可能於晚間或次一營業日補齊。",
        },
    }
