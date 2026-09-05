from __future__ import annotations

from fastapi import FastAPI

from api.line_webhook import router as line_webhook_router
from api.line_model_benchmark import router as line_model_benchmark_router
from core.line_bot_config import load_line_bot_env
from services.line_bot_service import line_bot_readiness
from services.conversation_memory_service import conversation_memory_service


load_line_bot_env()
conversation_memory_service()

app = FastAPI(
    title="Taiwan Stock LINE Bot Gateway",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(line_webhook_router)
app.include_router(line_model_benchmark_router)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, object]:
    return line_bot_readiness()
