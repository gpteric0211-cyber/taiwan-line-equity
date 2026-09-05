from __future__ import annotations

from fastapi import FastAPI

from api.bot_market_data import router as bot_market_data_router
from core.line_bot_config import load_line_bot_env


load_line_bot_env(
    allowed_names={
        "BOT_MARKET_DATA_TOKEN",
        "BOT_ENABLE_UNVERIFIED_TRADES",
        "TAIWAN50_DB_PATH",
    }
)


app = FastAPI(
    title="Taiwan50 Bot Market Data API",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(bot_market_data_router)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok", "mode": "read_only"}
