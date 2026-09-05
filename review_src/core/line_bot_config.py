from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from core.market_database_config import MARKET_DB_ENV, resolve_market_db_path


REVIEW_SRC = Path(__file__).resolve().parents[1]
PROJECT_ROOT = REVIEW_SRC.parent
LINE_BOT_ENV_FILE = PROJECT_ROOT / ".env.line_bot"
LEGACY_LINE_BOT_ENV_FILE = REVIEW_SRC / ".env.line_bot"


def resolve_line_bot_env_file() -> Path:
    """Prefer the project-root secret file while retaining legacy compatibility."""

    if LINE_BOT_ENV_FILE.is_file() or not LEGACY_LINE_BOT_ENV_FILE.is_file():
        return LINE_BOT_ENV_FILE
    return LEGACY_LINE_BOT_ENV_FILE


def load_line_bot_env(*, allowed_names: set[str] | None = None) -> Path:
    """Load the private LINE/Qwen env file without replacing explicit process env."""

    env_file = resolve_line_bot_env_file()
    market_db = resolve_market_db_path(base_dir=REVIEW_SRC, private_env_file=env_file)
    if allowed_names is None:
        load_dotenv(env_file, override=False)
    else:
        values = dotenv_values(env_file)
        for name in allowed_names:
            value = values.get(name)
            if value is not None:
                os.environ.setdefault(name, str(value))
    if allowed_names is None or MARKET_DB_ENV in allowed_names:
        os.environ[MARKET_DB_ENV] = str(market_db)
    return env_file


def env_text(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def env_bool(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return env_text(name, fallback).lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(env_text(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(env_text(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def configured_line_user_ids() -> set[str]:
    return {
        item.strip()
        for item in env_text("LINE_ALLOWED_USER_IDS").split(",")
        if item.strip()
    }
