from __future__ import annotations

import base64
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from core.market_database_config import resolve_market_db_path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
COMPONENTS_FILE = DATA_DIR / "taiwan50_components.csv"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
US_EASTERN = ZoneInfo("America/New_York")
YFINANCE_CACHE_DIR = DATA_DIR / "yfinance_cache"


if not ENV_FILE.exists() and ENV_EXAMPLE.exists():
    ENV_FILE.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
load_dotenv(ENV_FILE)


def default_portable_db_path() -> Path:
    """Portable default DB stored inside the extracted project folder."""
    return DATA_DIR / "taiwan50.db"


def resolve_db_path() -> Path:
    """Use the same market database selection as the read-only LINE Bot API."""
    return resolve_market_db_path(base_dir=ROOT)


def clean_api_token(value: str) -> str:
    """Accept either raw token or 'Bearer xxx' pasted by user; store/use raw token only."""
    token = (value or "").strip().strip('"').strip("'")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def token_decode_hint(value: str) -> str:
    token = clean_api_token(value)
    if not token:
        return "empty"
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8", errors="ignore").strip()
    except Exception:
        return "raw"
    if decoded and decoded != token and re.search(r"[A-Za-z0-9]", decoded):
        return "base64_like"
    return "raw"


def fugle_key_variants(value: str) -> list[str]:
    token = clean_api_token(value)
    variants: list[str] = []
    if token:
        variants.append(token)
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8", errors="ignore").strip()
    except Exception:
        decoded = ""
    if decoded:
        for candidate in [decoded, *decoded.split()]:
            candidate = clean_api_token(candidate)
            if candidate and candidate not in variants:
                variants.append(candidate)
    return variants


def mask_secret_text(text: str) -> str:
    """Prevent API tokens from leaking into UI status messages/log-style errors."""
    s = str(text or "")
    s = re.sub(r"(token=)([^&\s]+)", r"\1***", s, flags=re.IGNORECASE)
    s = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]+", r"\1***", s, flags=re.IGNORECASE)
    for secret in [os.getenv("FINMIND_TOKEN", ""), os.getenv("FUGLE_API_KEY", "")]:
        secret = clean_api_token(secret)
        if secret and len(secret) > 8:
            s = s.replace(secret, "***")
    return s


def safe_error(exc: Exception | str) -> str:
    return mask_secret_text(str(exc))


def configure_yfinance_cache(yf_module: Any | None = None) -> None:
    """Keep yfinance SQLite caches in the portable project data folder."""
    try:
        YFINANCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if yf_module is not None and hasattr(yf_module, "set_tz_cache_location"):
            yf_module.set_tz_cache_location(str(YFINANCE_CACHE_DIR))
        try:
            import yfinance.cache as yf_cache  # type: ignore
            if hasattr(yf_cache, "set_cache_location"):
                yf_cache.set_cache_location(str(YFINANCE_CACHE_DIR))
        except Exception:
            logging.exception("Failed to configure yfinance cache module")
    except Exception:
        logging.exception("Failed to create yfinance cache directory")


DB_PATH = resolve_db_path()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
LEGACY_DB_PATH = DATA_DIR / "dashboard.sqlite3"
if not DB_PATH.exists() and LEGACY_DB_PATH.exists() and LEGACY_DB_PATH.resolve() != DB_PATH.resolve():
    try:
        shutil.copy2(LEGACY_DB_PATH, DB_PATH)
    except Exception:
        logging.exception("Failed to copy legacy local DB to portable DB")


FUGLE_API_KEY = clean_api_token(os.getenv("FUGLE_API_KEY", ""))
FINMIND_TOKEN = clean_api_token(os.getenv("FINMIND_TOKEN", ""))
FINMIND_TOKEN_DISABLED_REASON = ""
FUGLE_WATCH_POLL_SECONDS = int(os.getenv("FUGLE_WATCH_POLL_SECONDS", "10"))
FUGLE_INTRADAY_MODE = os.getenv("FUGLE_INTRADAY_MODE", "disabled").strip().lower()
if FUGLE_INTRADAY_MODE not in {"websocket_candles", "websocket_trades", "rest_quote_polling", "disabled"}:
    FUGLE_INTRADAY_MODE = "disabled"
if not FUGLE_API_KEY:
    FUGLE_INTRADAY_MODE = "disabled"
FUGLE_INTRADAY_POLL_SECONDS = int(os.getenv("FUGLE_INTRADAY_POLL_SECONDS", "60"))
FUGLE_MAX_WATCHLIST_SYMBOLS = int(os.getenv("FUGLE_MAX_WATCHLIST_SYMBOLS", "5"))
DAILY_CHIP_MOMENTUM_KEEP_ROWS = int(os.getenv("DAILY_CHIP_MOMENTUM_KEEP_ROWS", "200"))
INTRADAY_1M_KEEP_DAYS = int(os.getenv("INTRADAY_1M_KEEP_DAYS", "30"))
TDCC_TREND_WEEKS = int(os.getenv("TDCC_TREND_WEEKS", "4"))
CHIP_SCORE_LOW_VOLUME_THRESHOLD = int(os.getenv("CHIP_SCORE_LOW_VOLUME_THRESHOLD", "500"))
MIS_AUTO_UPDATE_MARKET_HOURS = os.getenv("MIS_AUTO_UPDATE_MARKET_HOURS", "1").strip().lower() not in {"0", "false", "no", "off"}
MIS_POLL_SECONDS = float(os.getenv("MIS_POLL_SECONDS", "5"))
MIS_CACHE_TTL_SECONDS = float(os.getenv("MIS_CACHE_TTL_SECONDS", "8"))
EOD_PAGE_REFRESH_SECONDS = int(os.getenv("EOD_PAGE_REFRESH_SECONDS", "300"))
FINMIND_DELAY_SECONDS = float(os.getenv("FINMIND_DELAY_SECONDS", "0.6"))
FINMIND_HOURLY_SOFT_LIMIT = int(os.getenv("FINMIND_HOURLY_SOFT_LIMIT", "500"))
FINMIND_INCREMENTAL_DAYS = int(os.getenv("FINMIND_INCREMENTAL_DAYS", "30"))
FINMIND_BATCH_SIZE = int(os.getenv("FINMIND_BATCH_SIZE", "5"))
FINMIND_BATCH_SLEEP_SECONDS = float(os.getenv("FINMIND_BATCH_SLEEP_SECONDS", "4"))
YAHOO_REQUEST_SLEEP_SECONDS = float(os.getenv("YAHOO_REQUEST_SLEEP_SECONDS", "1.5"))
FUGLE_AUTO_UPDATE_MARKET_HOURS = os.getenv("FUGLE_AUTO_UPDATE_MARKET_HOURS", "1").strip().lower() not in {"0", "false", "no", "off"}
AUTO_REFRESH_MARKET_DATA_ON_START = os.getenv(
    "AUTO_REFRESH_MARKET_DATA_ON_START", "0"
).strip().lower() in {"1", "true", "yes", "on"}
AUTO_UPDATE_TW50_ON_START = os.getenv("AUTO_UPDATE_TW50_ON_START", "0").strip().lower() in {"1", "true", "yes", "on"}
MIS_SESSION_EVIDENCE_CODES = tuple(
    code.strip().zfill(4)
    for code in os.getenv("MIS_SESSION_EVIDENCE_CODES", "0050,2330").replace(";", ",").split(",")
    if code.strip().isdigit()
)
MIS_SESSION_EVIDENCE_MAX_AGE_SECONDS = max(
    60,
    int(os.getenv("MIS_SESSION_EVIDENCE_MAX_AGE_SECONDS", "300")),
)
TWSE_HOLIDAY_SCHEDULE_URL = os.getenv(
    "TWSE_HOLIDAY_SCHEDULE_URL",
    "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule",
).strip()
TWSE_HOLIDAY_SCHEDULE_HISTORY_URL = os.getenv(
    "TWSE_HOLIDAY_SCHEDULE_HISTORY_URL",
    "https://www.twse.com.tw/holidaySchedule/holidaySchedule",
).strip()
OFFICIAL_TAIWAN_MARKET_HOLIDAY_YEARS = {2026}
OFFICIAL_TAIWAN_EXTRAORDINARY_CLOSURES = {
    # TWSE emergency bulletins: the market was closed during Typhoon
    # KRATHON on 2024-10-02 and 2024-10-03, and during Typhoon KONG-REY
    # on 2024-10-31.  These ad-hoc closures are absent from the annual
    # holiday schedule and must be merged separately.
    "2024-10-02": "TWSE typhoon closure bulletin (KRATHON)",
    "2024-10-03": "TWSE typhoon closure bulletin (KRATHON)",
    "2024-10-31": "TWSE typhoon closure bulletin (KONG-REY)",
    # TWSE 2026-07-09 emergency bulletin: market closed on 2026-07-10
    # because Taipei City suspended work during Typhoon BAVI.
    "2026-07-10": "TWSE typhoon closure bulletin (BAVI)",
}
OFFICIAL_TAIWAN_EXTRAORDINARY_CLOSURE_URLS = {
    "2024-10-02": "https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d69236c2e301924806d5b4004e.pdf",
    "2024-10-03": "https://investoredu.twse.com.tw/FileSystem/FileUpload/2c6e50d4-a390-439c-a2c4-823937dee4b2.pdf",
    "2024-10-31": "https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d69236c2e30192dd5179bc0327.pdf",
    "2026-07-10": "https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d69ef76943019f46cbd3fd0112.pdf",
}
DEFAULT_TAIWAN_MARKET_HOLIDAYS = {
    # Complete official 2026 weekday no-trading dates from the TWSE annual
    # holiday schedule, plus the extraordinary 2026-07-10 typhoon closure.
    # Add later ad-hoc no-trading dates through TAIWAN_MARKET_HOLIDAYS.
    "2026-01-01",
    "2026-02-12",
    "2026-02-13",
    "2026-02-16",
    "2026-02-17",
    "2026-02-18",
    "2026-02-19",
    "2026-02-20",
    "2026-02-27",
    "2026-04-03",
    "2026-04-06",
    "2026-05-01",
    "2026-06-19",
    "2026-07-10",
    "2026-09-25",
    "2026-09-28",
    "2026-10-09",
    "2026-10-26",
    "2026-12-25",
}

TAIWAN_MARKET_HOLIDAYS = DEFAULT_TAIWAN_MARKET_HOLIDAYS | {
    x.strip()
    for x in os.getenv("TAIWAN_MARKET_HOLIDAYS", "").replace(";", ",").split(",")
    if x.strip()
}


FUGLE_BASE = "https://api.fugle.tw/marketdata/v1.0/stock"
TWSE_MIS_STOCK_INFO = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"
TDCC_HOLDING_DISTRIBUTION_CSV_URL = os.getenv(
    "TDCC_HOLDING_DISTRIBUTION_CSV_URL",
    "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5",
)
TWSE_STOCK_DAY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TWSE_STOCK_DAY_BY_CODE = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
TWSE_BWIBBU_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
TWSE_BWIBBU_D_OPENAPI = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_d"
TWSE_BWIBBU_D_RWD = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d"
TAIFEX_OPENAPI_BASE = "https://openapi.taifex.com.tw/v1"
TWSE_EX_DIVIDEND_CSV = "https://www.twse.com.tw/exchangeReport/TWT48U?response=csv"
TPEX_EX_DIVIDEND_CSV = "https://www.tpex.org.tw/zh-tw/announce/market/ex/announce.html?response=csv"
MA20_INVALID_DAYS_AFTER_EX = int(os.getenv("MA20_INVALID_DAYS_AFTER_EX", "5"))
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
