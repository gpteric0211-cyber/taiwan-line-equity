"""Side-effect-free market DB selection shared by web, Bot API and launchers.

Only the process environment and review_src/.env may select the market DB.
LINE's private env can repeat that path for compatibility, never override it.
This module must not import core.config/db (their bootstrap can create files).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values


MARKET_DB_ENV = "TAIWAN50_DB_PATH"


def _path(value: str | None, base_dir: Path) -> Path:
    text = str(value or "").strip().strip('"').strip("'")
    path = Path(text).expanduser() if text else Path("data") / "taiwan50.db"
    return (path if path.is_absolute() else base_dir / path).resolve()


def resolve_market_db_path(*, base_dir: Path, private_env_file: Path | None = None) -> Path:
    """Resolve without mkdir/copy/connect or changing environment variables.

    An explicit process setting (including blank = portable default) wins.
    Otherwise read the common .env without importing its other settings.
    Relative paths always use review_src, independent of the caller's cwd.
    A conflicting legacy private setting is an error before startup side effects.
    """
    value = os.environ.get(MARKET_DB_ENV)
    if MARKET_DB_ENV not in os.environ:
        common = base_dir / ".env"
        value = dotenv_values(common).get(MARKET_DB_ENV) if common.is_file() else None
    selected = _path(value, base_dir)
    if private_env_file is not None and private_env_file.is_file():
        private = dotenv_values(private_env_file).get(MARKET_DB_ENV)
        if private and _path(private, base_dir) != selected:
            raise ValueError(
                "market_database_configuration_conflict: configure TAIWAN50_DB_PATH "
                "in the shared market configuration; LINE private settings must not select another DB"
            )
    return selected
