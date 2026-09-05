from __future__ import annotations

"""Public projection helpers for market-data payloads.

Internal provenance remains available to repositories, services, audit logs and
quality gates. Normal web and LINE responses use this module to remove
implementation details that are not part of the user-facing contract.
"""

import re
from typing import Any


_INTERNAL_PROVENANCE_KEYS = {
    "data_source_issues",
    "data_sources",
    "db_path",
    "detail_source_context",
    "distribution_sources",
    "eps_source",
    "history_source",
    "mapping_location",
    "provider",
    "provider_name",
    "resolved_detail_source",
    "source",
    "source_hash",
    "source_key",
    "source_market",
    "source_meta",
    "source_name",
    "source_quality",
    "source_type",
    "valuation_source",
}

_ABSOLUTE_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:\\(?:[^\s<>|\"']+\\)*[^\s<>|\"']*")
_FILE_URI = re.compile(r"(?i)\bfile:(?://)?[^\s<>\"']+")
_PROVIDER_IMPLEMENTATION_PREFIXES = (
    "finmind_",
    "fugle_",
    "pchome_",
    "yahoo_",
)


def _is_internal_provenance_key(key: str) -> bool:
    lowered = key.lower()
    return bool(
        lowered in _INTERNAL_PROVENANCE_KEYS
        or lowered.startswith(_PROVIDER_IMPLEMENTATION_PREFIXES)
        or lowered == "sources"
        or "_source_" in lowered
        or lowered.startswith("source_")
        or lowered.endswith("_source")
        or lowered.endswith("_sources")
        or lowered.endswith("_source_meta")
        or lowered.endswith("_source_context")
    )


def _sanitize_public_text(value: str) -> str:
    """Mask machine-local paths while preserving ordinary market wording."""

    text = _ABSOLUTE_WINDOWS_PATH.sub("[本機路徑已隱藏]", value)
    return _FILE_URI.sub("[本機路徑已隱藏]", text)


def sanitize_public_market_payload(value: Any) -> Any:
    """Return a detached public view without internal provenance fields."""

    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_internal_provenance_key(key_text):
                continue
            output[key_text] = sanitize_public_market_payload(item)
        return output
    if isinstance(value, (list, tuple, set)):
        return [sanitize_public_market_payload(item) for item in value]
    if isinstance(value, str):
        return _sanitize_public_text(value)
    return value
