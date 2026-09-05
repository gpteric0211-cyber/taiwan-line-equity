from __future__ import annotations

"""Materialize a validated official exact session set outside request paths."""

import sqlite3
from typing import Any, Mapping

from adapter.official_calendar_revision import (
    OFFICIAL_SESSION_NORMALIZER_VERSION,
    normalize_official_calendar_revision_source,
)
from repository.single_track_v3_calendar_repository import (
    calendar_revision,
    seal_calendar_revision,
)


CALENDAR_MATERIALIZER_CONTRACT_VERSION = "SingleTrackV3CalendarMaterializerV1"


def materialize_official_calendar_revision(
    conn: sqlite3.Connection,
    payload: Mapping[str, Any],
    *,
    sealed_at: str,
) -> dict[str, Any]:
    """Validate then atomically seal one immutable official calendar revision."""

    normalized = normalize_official_calendar_revision_source(
        payload,
        sealed_at=sealed_at,
    )
    revision_id = str(normalized["revision"]["calendar_revision"])
    replayed = calendar_revision(conn, revision_id) is not None
    saved = seal_calendar_revision(
        conn,
        normalized["revision"],
        normalized["sessions"],
    )
    return {
        "contract_version": CALENDAR_MATERIALIZER_CONTRACT_VERSION,
        "normalizer_version": OFFICIAL_SESSION_NORMALIZER_VERSION,
        "calendar_revision": revision_id,
        "revision_digest": saved["revision_digest"],
        "session_count": len(saved["sessions"]),
        "session_states": sorted({item["session_state"] for item in saved["sessions"]}),
        "source_contract_digest": normalized["source_contract_digest"],
        "source_assertions": normalized["source_assertions"],
        "replayed": replayed,
        "zero_model_calls": 0,
        "production_source_fetches": 0,
    }
