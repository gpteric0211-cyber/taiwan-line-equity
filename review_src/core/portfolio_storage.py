"""Private portfolio storage configuration, independent of the market database."""

from __future__ import annotations
import os
from pathlib import Path
from cryptography.fernet import Fernet

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def configured_path(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default)).expanduser()
    return (value if value.is_absolute() else PROJECT_ROOT / value).resolve()


def database_path() -> Path:
    return configured_path("EQUITY_USER_DB", "var/private/portfolio.sqlite3")


def key_path() -> Path:
    return configured_path("EQUITY_USER_KEY_FILE", "var/private/portfolio.key")


def initialize_key() -> bytes:
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(Fernet.generate_key())
        if os.name != "nt":
            path.chmod(0o600)
    except FileExistsError:
        pass
    key = path.read_bytes().strip()
    Fernet(key)
    return key


def load_key() -> bytes:
    key = key_path().read_bytes().strip()
    Fernet(key)
    return key
