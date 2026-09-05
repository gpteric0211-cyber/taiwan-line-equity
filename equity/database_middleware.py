"""Keep each HTTP analysis on one publication and refresh its derived caches."""

from __future__ import annotations

import hashlib
import threading

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse


class MarketSnapshotMiddleware:
    def __init__(self, app, database):
        self.app = app
        self.database = database
        self.version = None
        self.lock = threading.Lock()

    def refresh(self):
        from core import cache

        stat = self.database.stat()
        version = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        with self.lock:
            if self.version != version:
                for values, lock in [(cache._row_cache, cache._row_cache_lock),
                                     (cache._score_cache, cache._score_cache_lock),
                                     (cache._practical_cache, cache._practical_cache_lock)]:
                    with lock:
                        values.clear()
                self.version = version
        return hashlib.sha256(repr(version).encode()).hexdigest()[:16]

    async def __call__(self, scope, receive, send):
        # LINE ingress and health remain available even during the short switch.
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        # Canonical/shadow analysis seals artifacts in the market DB. It must
        # not invalidate a candidate built by the scheduled/manual updater.
        if scope.get("method") == "POST" and scope["path"].startswith("/api/bot/market-data/analysis"):
            from scripts.run_isolated_post_close_pipeline import DEFAULT_LOCK, isolated_update_lock

            with isolated_update_lock(DEFAULT_LOCK) as acquired:
                if not acquired:
                    return await JSONResponse(
                        {"detail": "行情更新進行中，分析產物保存請稍後重試"}, status_code=503,
                        headers={"Retry-After": "2"})(scope, receive, send)
                return await self.market_request(scope, receive, send)
        return await self.market_request(scope, receive, send)

    async def market_request(self, scope, receive, send):
        from core.database_access import DatabaseLease, DatabaseBusyError

        lease = DatabaseLease(self.database)
        try:
            await run_in_threadpool(lease.acquire)
        except DatabaseBusyError:
            return await JSONResponse({"detail": "行情資料正在發布，請稍後重試"}, status_code=503,
                                      headers={"Retry-After": "2"})(scope, receive, send)
        try:
            generation = await run_in_threadpool(self.refresh)

            async def send_version(message):
                if message["type"] == "http.response.start":
                    message.setdefault("headers", []).append((b"x-market-generation", generation.encode()))
                await send(message)

            await self.app(scope, receive, send_version)
        finally:
            lease.close()
