from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.line_bot_config import PROJECT_ROOT, REVIEW_SRC, env_bool, env_int, env_text


DEFAULT_RAW_RETENTION_SECONDS = 24 * 60 * 60
DEFAULT_SUMMARY_RETENTION_SECONDS = 30 * 24 * 60 * 60


def _configured_path(name: str, default: Path) -> Path:
    configured = env_text(name)
    if not configured:
        return default.resolve()
    candidate = Path(configured).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


@dataclass(frozen=True)
class LineMemorySettings:
    storage: str
    database_path: Path
    key_file: Path
    channel_namespace: str
    raw_retention_seconds: int
    summary_retention_seconds: int
    recent_exchange_limit: int
    prompt_character_budget: int
    compaction_trigger: int
    compaction_batch_size: int
    compaction_timeout_seconds: int
    privacy_notice_version: str
    long_term_approved: bool

    @property
    def persistent_enabled(self) -> bool:
        return self.storage == "sqlite"

    @property
    def signature(self) -> tuple[object, ...]:
        return (
            self.storage,
            str(self.database_path),
            str(self.key_file),
            self.channel_namespace,
            self.raw_retention_seconds,
            self.summary_retention_seconds,
            self.recent_exchange_limit,
            self.prompt_character_budget,
            self.compaction_trigger,
            self.compaction_batch_size,
            self.compaction_timeout_seconds,
            self.privacy_notice_version,
            self.long_term_approved,
        )


def line_memory_settings() -> LineMemorySettings:
    storage = env_text("LINE_MEMORY_STORAGE", "memory").lower()
    if storage not in {"memory", "sqlite"}:
        storage = "memory"
    privacy_version = env_text("LINE_MEMORY_PRIVACY_NOTICE_VERSION")
    long_term_approved = bool(
        env_bool("LINE_MEMORY_LONG_TERM_APPROVED", False) and privacy_version
    )
    retention_maximum = 90 * 24 * 60 * 60 if long_term_approved else 24 * 60 * 60
    raw_retention = env_int(
        "LINE_MEMORY_RAW_RETENTION_SECONDS",
        DEFAULT_RAW_RETENTION_SECONDS,
        minimum=600,
        maximum=90 * 24 * 60 * 60,
    )
    summary_retention = env_int(
        "LINE_MEMORY_SUMMARY_RETENTION_SECONDS",
        DEFAULT_SUMMARY_RETENTION_SECONDS,
        minimum=600,
        maximum=90 * 24 * 60 * 60,
    )
    return LineMemorySettings(
        storage=storage,
        database_path=_configured_path(
            "LINE_MEMORY_DB_PATH",
            REVIEW_SRC / "data" / "line_conversation_memory.sqlite3",
        ),
        key_file=_configured_path(
            "LINE_MEMORY_KEY_FILE",
            REVIEW_SRC / "data" / "line_conversation_memory.key",
        ),
        channel_namespace=env_text("LINE_MEMORY_CHANNEL_NAMESPACE", "primary-line-channel"),
        raw_retention_seconds=min(raw_retention, retention_maximum),
        summary_retention_seconds=min(summary_retention, retention_maximum),
        recent_exchange_limit=env_int(
            "LINE_MEMORY_RECENT_EXCHANGES", 8, minimum=2, maximum=20
        ),
        prompt_character_budget=env_int(
            "LINE_MEMORY_PROMPT_CHARACTER_BUDGET", 6000, minimum=1200, maximum=12000
        ),
        compaction_trigger=env_int(
            "LINE_MEMORY_COMPACTION_TRIGGER", 3, minimum=3, maximum=30
        ),
        compaction_batch_size=env_int(
            "LINE_MEMORY_COMPACTION_BATCH_SIZE", 6, minimum=2, maximum=20
        ),
        compaction_timeout_seconds=env_int(
            "LINE_MEMORY_COMPACTION_TIMEOUT_SECONDS", 60, minimum=20, maximum=120
        ),
        privacy_notice_version=privacy_version,
        long_term_approved=long_term_approved,
    )
