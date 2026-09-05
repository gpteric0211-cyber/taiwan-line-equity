from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from core.status import get_status
from core.utils import now_tpe


router = APIRouter()


@router.get("/api/status")
def api_status() -> dict[str, Any]:
    return {"statuses": get_status(), "now": now_tpe().isoformat()}
