from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.official_calendar_revision import (  # noqa: E402
    OFFICIAL_SESSION_SOURCE_CONTRACT_VERSION,
    normalize_official_calendar_revision_source,
)
from core.single_track_v3_schema import ensure_single_track_v3_schema  # noqa: E402
from repository.single_track_v3_calendar_repository import (  # noqa: E402
    calendar_revision,
    latest_calendar_revision_at,
)
from task.single_track_v3_calendar_materializer import (  # noqa: E402
    materialize_official_calendar_revision,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_single_track_v3_schema(conn)
    conn.commit()
    return conn


def _payload() -> dict:
    specs = (
        ("2026-09-07", "scheduled", "09:00", "13:30", None),
        ("2026-09-08", "cancelled", None, None, "official weather closure"),
        ("2026-09-09", "delayed", "10:00", "13:30", None),
        ("2026-09-10", "special_session", "11:00", "12:00", None),
        ("2026-09-11", "early_close", "09:00", "11:30", None),
    )
    return {
        "contract_version": OFFICIAL_SESSION_SOURCE_CONTRACT_VERSION,
        "source_type": "official_exact_session_schedule",
        "authority_tier": "canonical_official",
        "source_id": "TWSE_EXACT_SESSION_BULLETIN",
        "source_url": "https://www.twse.com.tw/official/session-schedule",
        "source_document_digest": _digest("official-source-document-r1"),
        "published_at": "2026-09-01T09:00:00+08:00",
        "available_at": "2026-09-01T09:01:00+08:00",
        "timezone": "Asia/Taipei",
        "session_policy_version": "TWSEOfficialExactSessionPolicyV1",
        "sessions": [
            {
                "trade_date": trade_date,
                "session_state": state,
                "scheduled_open_at": (
                    f"{trade_date}T{opened}:00+08:00" if opened else None
                ),
                "scheduled_close_at": (
                    f"{trade_date}T{closed}:00+08:00" if closed else None
                ),
                "cancellation_reason": cancellation_reason,
                "official_state_explicit": True,
                "official_open_close_explicit": state != "cancelled",
                "source_evidence_digest": _digest(
                    f"official-session:{trade_date}:{state}:{opened}:{closed}"
                ),
            }
            for trade_date, state, opened, closed, cancellation_reason in specs
        ],
    }


def test_exact_official_session_set_materializes_and_replays_immutably() -> None:
    conn = _connection()
    first = materialize_official_calendar_revision(
        conn,
        _payload(),
        sealed_at="2026-09-01T09:02:00+08:00",
    )
    conn.commit()
    replay = materialize_official_calendar_revision(
        conn,
        _payload(),
        sealed_at="2026-09-01T09:02:00+08:00",
    )

    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert replay["revision_digest"] == first["revision_digest"]
    assert first["session_count"] == 5
    assert first["session_states"] == [
        "cancelled",
        "delayed",
        "early_close",
        "scheduled",
        "special_session",
    ]
    assert first["source_assertions"] == {
        "exact_session_set": True,
        "heuristic_regular_hours_used": False,
        "holiday_only_source_used": False,
        "official_state_required_per_session": True,
        "official_open_close_required_per_open_session": True,
    }
    saved = calendar_revision(conn, first["calendar_revision"])
    assert saved is not None
    by_date = {item["trade_date"]: item for item in saved["sessions"]}
    assert by_date["2026-09-08"]["scheduled_open_at"] is None
    assert by_date["2026-09-08"]["cancellation_reason"] == "official weather closure"
    assert by_date["2026-09-09"]["scheduled_open_at"].endswith("10:00:00+08:00")
    assert by_date["2026-09-10"]["scheduled_close_at"].endswith("12:00:00+08:00")
    assert by_date["2026-09-11"]["scheduled_close_at"].endswith("11:30:00+08:00")


def test_calendar_revision_is_not_point_in_time_visible_before_availability() -> None:
    conn = _connection()
    result = materialize_official_calendar_revision(
        conn,
        _payload(),
        sealed_at="2026-09-01T09:02:00+08:00",
    )
    assert latest_calendar_revision_at(
        conn,
        visible_at="2026-09-01T09:00:59+08:00",
    ) is None
    visible = latest_calendar_revision_at(
        conn,
        visible_at="2026-09-01T09:02:00+08:00",
    )
    assert visible["calendar_revision"] == result["calendar_revision"]


def test_holiday_only_payload_is_rejected_instead_of_inventing_regular_hours() -> None:
    payload = _payload()
    payload.pop("sessions")
    payload["closure_dates"] = ["2026-09-08"]
    payload["open_reference_dates"] = ["2026-09-07"]

    with pytest.raises(ValueError, match="holiday/closure dates alone are insufficient"):
        normalize_official_calendar_revision_source(
            payload,
            sealed_at="2026-09-01T09:02:00+08:00",
        )


def test_open_session_requires_explicit_official_open_close_evidence() -> None:
    payload = _payload()
    payload["sessions"][0]["official_open_close_explicit"] = False

    with pytest.raises(ValueError, match="explicit official open/close evidence"):
        normalize_official_calendar_revision_source(
            payload,
            sealed_at="2026-09-01T09:02:00+08:00",
        )


def test_untrusted_or_non_https_calendar_source_fails_closed() -> None:
    payload = _payload()
    payload["authority_tier"] = "news_radar"
    with pytest.raises(ValueError, match="canonical_official"):
        normalize_official_calendar_revision_source(
            payload,
            sealed_at="2026-09-01T09:02:00+08:00",
        )

    payload = _payload()
    payload["source_url"] = "http://example.com/not-official"
    with pytest.raises(ValueError, match="absolute HTTPS"):
        normalize_official_calendar_revision_source(
            payload,
            sealed_at="2026-09-01T09:02:00+08:00",
        )
