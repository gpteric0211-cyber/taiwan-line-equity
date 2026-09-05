from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from core.cache import _row_cache, _row_cache_lock
from core.components import resolve_stock
from repository.watchlist_repository import (
    delete_watchlist_item,
    list_watchlist_items,
    upsert_watchlist_item_with_limit,
    watchlist_contains,
)
from services.stock_detail_service import find_stock_item


router = APIRouter()
_enqueue_watchlist_bootstrap: Callable[[str], dict[str, Any]] | None = None


def configure_watchlist_router(enqueue_watchlist_bootstrap: Callable[[str], dict[str, Any]]) -> None:
    global _enqueue_watchlist_bootstrap
    _enqueue_watchlist_bootstrap = enqueue_watchlist_bootstrap


@router.get("/api/watchlist")
def api_watchlist() -> dict[str, Any]:
    rows = list_watchlist_items()
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name or name.lower() in {"null", "none", "undefined", "nan"}:
            resolved = find_stock_item(str(row.get("code") or ""))
            if resolved and resolved.get("name"):
                row["name"] = resolved["name"]
    return {"items": rows, "limit": 5}


@router.post("/api/watchlist")
def api_add_watchlist(payload: dict[str, Any] = Body(...)) -> JSONResponse:
    query = str(payload.get("code") or payload.get("query") or "").strip()
    item = resolve_stock(query)
    if not item:
        return JSONResponse({"ok": False, "error": "找不到股票，請輸入 4 碼代號或名稱。"}, status_code=400)
    already_exists = watchlist_contains(item["code"])
    ok, error, _added_item = upsert_watchlist_item_with_limit(item["code"], item["name"], 5, time.time())
    if not ok:
        return JSONResponse({"ok": False, "error": error}, status_code=400)
    with _row_cache_lock:
        _row_cache.pop(f"watchlist:{item['code']}", None)
    if _enqueue_watchlist_bootstrap is None:
        raise RuntimeError("watchlist router is not configured")
    bootstrap = _enqueue_watchlist_bootstrap(item["code"])
    bootstrap_status = bootstrap.get("bootstrap_status", "manual_required")
    readiness = bootstrap.get("readiness")
    if bootstrap_status == "queued":
        message = "已加入自選股，系統將補齊分析資料；補齊前 detail 可能只顯示即時報價。"
    elif bootstrap_status == "already_ready":
        message = "已加入自選股，完整分析資料已可使用。"
    elif bootstrap_status == "unsupported":
        message = "已加入自選股；目前暫不支援此市場資料，先不顯示半成品分析。"
    else:
        message = "已加入自選股；完整分析需先執行資料補齊流程。"
    return JSONResponse({
        "ok": True,
        "item": item,
        "code": item["code"],
        "added": not already_exists,
        "already_exists": already_exists,
        "is_watchlist": True,
        "preload_started": bootstrap_status == "queued",
        "bootstrap_status": bootstrap_status,
        "readiness": readiness,
        "retry_after_seconds": 5 if bootstrap_status in {"queued", "running"} else None,
        "message": message,
    })


@router.delete("/api/watchlist/{code}")
def api_delete_watchlist(code: str) -> dict[str, Any]:
    delete_watchlist_item(code)
    return {"ok": True}
