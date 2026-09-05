from __future__ import annotations

import threading
import time
from typing import Any

from core.config import mask_secret_text
from repository.status_repository import get_fetch_status, set_fetch_status


_status_lock = threading.RLock()


def set_status(key: str, status: str, message: str = "") -> None:
    with _status_lock:
        set_fetch_status(key, status, mask_secret_text(message), time.time())


def get_status() -> dict[str, Any]:
    return get_fetch_status()
