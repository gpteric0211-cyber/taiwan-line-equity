"""One HTTP service for mobile UI, internal data API, and signed LINE ingress."""

from __future__ import annotations
from equity import bootstrap

bootstrap()


def create_app():
    from core.line_bot_config import load_line_bot_env

    load_line_bot_env()
    from core.tls_config import configure_tls

    configure_tls()
    from app import app
    from api.bot_market_data import router as market_router
    from api.line_webhook import router as webhook_router
    from api.local_setup import router as setup_router, local_request
    from auth.dependencies import get_current_user
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
    from fastapi.security import HTTPAuthorizationCredentials
    from starlette.concurrency import run_in_threadpool

    if not getattr(app.state, "equity_integrated", False):
        app.include_router(setup_router)

        @app.middleware("http")
        async def protect_api(request, call_next):
            path = request.url.path
            public = path.startswith(("/api/auth/", "/api/bot/market-data/")) or path == "/api/setup"
            if path.startswith("/api/") and not public:
                value = request.headers.get("authorization", "")
                credentials = (
                    HTTPAuthorizationCredentials(scheme="Bearer", credentials=value[7:])
                    if value.lower().startswith("bearer ")
                    else None
                )
                try:
                    await run_in_threadpool(get_current_user, request, credentials)
                except HTTPException as exc:
                    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
                if (
                    path.startswith("/api/config")
                    and request.method not in {"GET", "HEAD"}
                    and not local_request(request)
                ):
                    return JSONResponse({"detail": "系統設定僅能在主機本機修改"}, status_code=403)
            response = await call_next(request)
            if path.startswith("/api/"):
                response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            return response

        app.include_router(market_router)
        app.include_router(webhook_router)

        @app.get("/healthz", include_in_schema=False)
        def health():
            return {"status": "ok", "service": "taiwan-line-equity"}

        app.state.equity_integrated = True
        app.title = "Taiwan Line Equity"
        app.version = "0.1.0"
    return app
