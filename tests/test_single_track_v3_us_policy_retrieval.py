from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.single_track_v3_us_policy_retrieval import (  # noqa: E402
    BIS_PRESS_RELEASES_URL,
    BIS_SOURCE_ID,
    OFAC_RECENT_ACTIONS_URL,
    OFAC_SOURCE_ID,
    US_POLICY_SANCTIONS_ADAPTER_VERSION,
    US_POLICY_SANCTIONS_RETRIEVAL_SOURCE_ID,
    US_TREASURY_PRESS_RELEASES_URL,
    US_TREASURY_SOURCE_ID,
    build_us_policy_sanctions_retrieval_fetcher,
)
from core.single_track_v3_event_source_plan import EVENT_SOURCE_PLAN_VERSION  # noqa: E402
from core.single_track_v3_schema import ensure_single_track_v3_schema  # noqa: E402
from repository.single_track_v3_research_repository import (  # noqa: E402
    research_news_item,
)
from repository.single_track_v3_retrieval_repository import (  # noqa: E402
    retrieval_worker_receipt,
)
from repository.single_track_v3_scheduler_repository import (  # noqa: E402
    create_news_retrieval_run,
)
from task.single_track_v3_event_reconciler import reconcile_research_events  # noqa: E402
from task.single_track_v3_retrieval_worker import (  # noqa: E402
    RetrievalSourceSpec,
    run_retrieval_worker,
)


TPE = ZoneInfo("Asia/Taipei")
TREASURY_TITLE = "Treasury Announces Semiconductor Supply-Chain Policy"
OFAC_TITLE = "Iran-related and Counter Terrorism Designations"
BIS_TITLE = "Department of Commerce Strengthens Semiconductor Export Controls"


class Response:
    def __init__(self, content: bytes, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error


class IncrementingClock:
    def __init__(self, value: str) -> None:
        self.current = datetime.fromisoformat(value)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


def _treasury_html(*, link: str = "/news/press-releases/sb123") -> bytes:
    return f"""<!doctype html><html><body>
<div class="date">August 31, 2026</div>
<h3><a href="{link}">{TREASURY_TITLE}</a></h3>
<p>Press-release body must not enter the metadata lane.</p>
</body></html>""".encode("utf-8")


def _ofac_html() -> bytes:
    return f"""<!doctype html><html><body>
<h3><a href="/recent-actions/20260828">{OFAC_TITLE}</a></h3>
<div>August 28, 2026 - Sanctions List Updates</div>
<p>Designation body must not enter the metadata lane.</p>
    </body></html>""".encode("utf-8")


def _bis_html(*, link: str = "/press-release/commerce-strengthens-controls") -> bytes:
    return f"""<!doctype html><html><body>
<div>August 29, 2026</div>
<h3><a href="{link}">{BIS_TITLE}</a></h3>
<p>Full BIS index excerpt must not enter the metadata lane.</p>
</body></html>""".encode("utf-8")


def _http_get(url: str, **_kwargs) -> Response:
    if url == US_TREASURY_PRESS_RELEASES_URL:
        return Response(_treasury_html())
    if url == OFAC_RECENT_ACTIONS_URL:
        return Response(_ofac_html())
    if url == BIS_PRESS_RELEASES_URL:
        return Response(_bis_html())
    raise AssertionError(f"unexpected URL: {url}")


def _run() -> dict:
    return {
        "run_id": "us-policy-run",
        "idempotency_key": "idempotency:us-policy-run",
        "slot_key": "evening_1800",
        "target_trade_date": "2026-09-02",
        "scheduled_for": "2026-09-01T18:00:00+08:00",
        "cutoff_at": "2026-09-01T18:15:00+08:00",
        "started_at": None,
        "completed_at": None,
        "status": "queued",
        "source_policy_version": "SourceAuthorityPolicyV1",
        "calendar_revision": "official-calendar-r1",
        "source_coverage": {},
        "source_failures": [],
        "late_reason": None,
        "created_at": "2026-09-01T17:59:00+08:00",
        "updated_at": "2026-09-01T17:59:00+08:00",
    }


def test_us_official_indexes_retain_title_date_link_only() -> None:
    calls: list[tuple[str, dict]] = []

    def http_get(url: str, **kwargs) -> Response:
        calls.append((url, kwargs))
        return _http_get(url, **kwargs)

    result = build_us_policy_sanctions_retrieval_fetcher(
        ["2454"],
        http_get=http_get,
        clock=lambda: datetime(2026, 9, 1, 18, 0, tzinfo=TPE),
    )("")

    assert [url for url, _ in calls] == [
        US_TREASURY_PRESS_RELEASES_URL,
        OFAC_RECENT_ACTIONS_URL,
        BIS_PRESS_RELEASES_URL,
    ]
    assert all(kwargs["allow_redirects"] is False for _, kwargs in calls)
    assert result["adapter_version"] == US_POLICY_SANCTIONS_ADAPTER_VERSION
    assert result["status"] == "ok"
    assert result["source_id"] == US_POLICY_SANCTIONS_RETRIEVAL_SOURCE_ID
    assert result["raw_article_bodies_fetched"] == 0
    assert result["canonical_table_writes"] == 0
    assert len(result["events"]) == 3
    assert {event["rights"]["source_id"] for event in result["events"]} == {
        US_TREASURY_SOURCE_ID,
        OFAC_SOURCE_ID,
        BIS_SOURCE_ID,
    }
    assert {event["event_date"] for event in result["events"]} == {
        "2026-08-31",
        "2026-08-28",
        "2026-08-29",
    }
    assert all(event["publisher_published_at"] is None for event in result["events"])
    assert all(event["publisher_time_verified"] is False for event in result["events"])
    assert all(event["entity_refs"] == ["2454"] for event in result["events"])
    assert "Press-release body" not in str(result["events"])
    assert "Designation body" not in str(result["events"])
    assert "Full BIS index excerpt" not in str(result["events"])


def test_us_official_indexes_preserve_ofac_when_treasury_times_out() -> None:
    def http_get(url: str, **_kwargs) -> Response:
        if url == US_TREASURY_PRESS_RELEASES_URL:
            raise requests.Timeout("controlled timeout")
        if url == OFAC_RECENT_ACTIONS_URL:
            return Response(_ofac_html())
        return Response(_bis_html())

    result = build_us_policy_sanctions_retrieval_fetcher(
        ["2454"],
        http_get=http_get,
        clock=lambda: datetime(2026, 9, 1, 18, 0, tzinfo=TPE),
    )("")

    assert result["ok"] is False
    assert result["status"] == "partial"
    assert len(result["events"]) == 2
    assert {event["rights"]["source_id"] for event in result["events"]} == {
        OFAC_SOURCE_ID,
        BIS_SOURCE_ID,
    }
    attempts = {item["source_id"]: item for item in result["source_attempts"]}
    assert attempts[US_TREASURY_SOURCE_ID]["status"] == "timeout"
    assert attempts[US_TREASURY_SOURCE_ID]["timeout_class"] == "source_timeout"
    assert attempts[OFAC_SOURCE_ID]["status"] == "ok"
    assert attempts[BIS_SOURCE_ID]["status"] == "ok"


def test_us_official_indexes_reject_redirect_and_nonofficial_detail_host() -> None:
    def http_get(url: str, **_kwargs) -> Response:
        if url == US_TREASURY_PRESS_RELEASES_URL:
            return Response(b"", status_code=302)
        if url == OFAC_RECENT_ACTIONS_URL:
            return Response(
                _ofac_html().replace(
                    b"/recent-actions/20260828",
                    b"https://attacker.example/recent-actions/20260828",
                )
            )
        return Response(_bis_html(link="https://attacker.example/press-release/fake"))

    result = build_us_policy_sanctions_retrieval_fetcher(
        ["2454"],
        http_get=http_get,
        clock=lambda: datetime(2026, 9, 1, 18, 0, tzinfo=TPE),
    )("")

    assert result["ok"] is False
    assert result["events"] == []
    assert [item["status"] for item in result["source_attempts"]] == [
        "redirect_rejected",
        "invalid_response",
        "invalid_response",
    ]


def test_us_official_worker_and_reconciler_keep_assessment_downstream() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_single_track_v3_schema(conn)
    create_news_retrieval_run(conn, _run(), ensure_schema=False)
    fetcher = build_us_policy_sanctions_retrieval_fetcher(
        ["2454"],
        http_get=_http_get,
        clock=lambda: datetime(2026, 9, 1, 18, 0, 2, tzinfo=TPE),
    )

    worker = run_retrieval_worker(
        conn,
        run_id="us-policy-run",
        queries=["2454 U.S. policy sanctions"],
        entity_refs=["2454"],
        source_specs=[
            RetrievalSourceSpec(
                source_id=US_POLICY_SANCTIONS_RETRIEVAL_SOURCE_ID,
                scope_key="us_policy_geopolitics",
                fetcher=fetcher,
                query_mode="once_per_run",
                authority_tier="canonical_official",
            )
        ],
        query_plan_version="SingleTrackV3EventQueryPlanV1",
        source_plan_version=EVENT_SOURCE_PLAN_VERSION,
        clock=IncrementingClock("2026-09-01T18:00:01+08:00"),
    )

    assert worker["status"] == "success"
    receipt = retrieval_worker_receipt(conn, "us-policy-run")
    assert receipt is not None
    assert len(receipt["news_item_ids"]) == 3
    items = [research_news_item(conn, item_id) for item_id in receipt["news_item_ids"]]
    assert all(item is not None for item in items)
    assert {item["source_id"] for item in items if item is not None} == {
        US_TREASURY_SOURCE_ID,
        OFAC_SOURCE_ID,
        BIS_SOURCE_ID,
    }
    assert all(item["publisher_time_verified"] is False for item in items if item)
    assert {item["publisher_published_date"] for item in items if item} == {
        "2026-08-28",
        "2026-08-29",
        "2026-08-31",
    }
    assert conn.execute("SELECT COUNT(*) FROM canonical_event_evidence").fetchone()[0] == 0

    reconciled = reconcile_research_events(
        conn,
        analysis_cutoff="2026-09-01T18:05:00+08:00",
        sealed_at="2026-09-01T18:06:00+08:00",
    )
    assert len(reconciled["promoted_event_revision_ids"]) == 3
    revisions = conn.execute("SELECT event_type,materiality FROM event_revision").fetchall()
    assert {(row["event_type"], row["materiality"]) for row in revisions} == {
        ("government_policy", "unknown")
    }
    assert reconciled["zero_model_calls"] == 0
    assert conn.execute("SELECT COUNT(*) FROM content_assessment").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM canonical_event_evidence").fetchone()[0] == 0
    conn.close()
