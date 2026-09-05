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

from adapter.single_track_v3_federal_reserve_retrieval import (  # noqa: E402
    FEDERAL_RESERVE_MONETARY_POLICY_RSS_URL,
    FEDERAL_RESERVE_RETRIEVAL_ADAPTER_VERSION,
    FEDERAL_RESERVE_SOURCE_ID,
    build_federal_reserve_retrieval_fetcher,
)
from core.single_track_v3_schema import ensure_single_track_v3_schema  # noqa: E402
from repository.single_track_v3_reconciliation_repository import (  # noqa: E402
    latest_event_revision_for_cluster,
)
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
TITLE = "Federal Reserve issues FOMC statement"
OFFICIAL_LINK = (
    "https://www.federalreserve.gov/newsevents/pressreleases/"
    "monetary20260901a.htm"
)


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


def _feed(*, prefix: bytes = b"") -> bytes:
    return prefix + f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><item>
  <title>{TITLE}</title>
  <link>{OFFICIAL_LINK}</link>
  <pubDate>Tue, 01 Sep 2026 09:45:00 GMT</pubDate>
  <description>Body text must never enter the research lane.</description>
</item></channel></rss>""".encode("utf-8")


def _run() -> dict:
    return {
        "run_id": "federal-reserve-run",
        "idempotency_key": "idempotency:federal-reserve-run",
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


def test_federal_reserve_feed_maps_only_bounded_official_metadata() -> None:
    calls: list[tuple[str, dict]] = []

    def http_get(url: str, **kwargs):
        calls.append((url, kwargs))
        return Response(_feed())

    fetcher = build_federal_reserve_retrieval_fetcher(
        ["2454"],
        http_get=http_get,
        clock=lambda: datetime(2026, 9, 1, 18, 0, tzinfo=TPE),
    )

    result = fetcher("")

    assert calls[0][0] == FEDERAL_RESERVE_MONETARY_POLICY_RSS_URL
    assert calls[0][1]["allow_redirects"] is False
    assert result["adapter_version"] == FEDERAL_RESERVE_RETRIEVAL_ADAPTER_VERSION
    assert result["status"] == "ok"
    assert result["source_id"] == FEDERAL_RESERVE_SOURCE_ID
    assert result["raw_article_bodies_fetched"] == 0
    assert result["canonical_table_writes"] == 0
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["title"] == TITLE
    assert event["url"] == OFFICIAL_LINK
    assert event["publisher_published_at"] == "2026-09-01T17:45:00+08:00"
    assert event["publisher_time_verified"] is True
    assert event["entity_refs"] == ["2454"]
    assert "description" not in event
    assert "Body text" not in str(event)


def test_federal_reserve_feed_types_timeout_without_fabricating_events() -> None:
    def http_get(*_args, **_kwargs):
        raise requests.Timeout("controlled timeout")

    result = build_federal_reserve_retrieval_fetcher(
        ["2454"],
        http_get=http_get,
        clock=lambda: datetime(2026, 9, 1, 18, 0, tzinfo=TPE),
    )("")

    assert result["ok"] is False
    assert result["status"] == "timeout"
    assert result["timeout_class"] == "source_timeout"
    assert result["events"] == []


def test_federal_reserve_feed_rejects_redirect_and_late_doctype() -> None:
    redirect = build_federal_reserve_retrieval_fetcher(
        ["2454"],
        http_get=lambda *_args, **_kwargs: Response(b"", status_code=302),
        clock=lambda: datetime(2026, 9, 1, 18, 0, tzinfo=TPE),
    )("")
    unsafe = build_federal_reserve_retrieval_fetcher(
        ["2454"],
        http_get=lambda *_args, **_kwargs: Response(
            b" " * 5000 + b"<!DOCTYPE rss [<!ENTITY xxe SYSTEM 'file:///x'>]><rss/>"
        ),
        clock=lambda: datetime(2026, 9, 1, 18, 0, tzinfo=TPE),
    )("")

    assert redirect["status"] == "redirect_rejected"
    assert redirect["events"] == []
    assert unsafe["status"] == "unsafe_xml_rejected"
    assert unsafe["events"] == []


def test_federal_reserve_worker_and_reconciler_keep_promotion_separate() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_single_track_v3_schema(conn)
    create_news_retrieval_run(conn, _run(), ensure_schema=False)
    fetcher = build_federal_reserve_retrieval_fetcher(
        ["2454"],
        http_get=lambda *_args, **_kwargs: Response(_feed()),
        clock=lambda: datetime(2026, 9, 1, 18, 0, 2, tzinfo=TPE),
    )

    worker = run_retrieval_worker(
        conn,
        run_id="federal-reserve-run",
        queries=["2454 Federal Reserve"],
        entity_refs=["2454"],
        source_specs=[
            RetrievalSourceSpec(
                source_id=FEDERAL_RESERVE_SOURCE_ID,
                scope_key="us_policy_geopolitics",
                fetcher=fetcher,
                query_mode="once_per_run",
                authority_tier="canonical_official",
            )
        ],
        query_plan_version="SingleTrackV3EventQueryPlanV1",
        source_plan_version="SingleTrackV3EventSourcePlanV1",
        clock=IncrementingClock("2026-09-01T18:00:01+08:00"),
    )

    assert worker["status"] == "success"
    receipt = retrieval_worker_receipt(conn, "federal-reserve-run")
    assert receipt is not None
    item = research_news_item(conn, receipt["news_item_ids"][0])
    assert item is not None
    assert item["source_id"] == FEDERAL_RESERVE_SOURCE_ID
    assert item["source_class"] == "canonical_official"
    assert item["verification_state"] == "primary_verified"
    assert conn.execute("SELECT COUNT(*) FROM canonical_event_evidence").fetchone()[0] == 0

    reconciled = reconcile_research_events(
        conn,
        analysis_cutoff="2026-09-01T18:05:00+08:00",
        sealed_at="2026-09-01T18:06:00+08:00",
    )
    revision_id = reconciled["promoted_event_revision_ids"][0]
    cluster_id = conn.execute(
        "SELECT event_cluster_id FROM event_revision WHERE event_revision_id=?",
        (revision_id,),
    ).fetchone()[0]
    revision = latest_event_revision_for_cluster(conn, cluster_id)
    assert revision is not None
    assert revision["event_type"] == "government_policy"
    assert revision["materiality"] == "unknown"
    assert reconciled["zero_model_calls"] == 0
    assert conn.execute("SELECT COUNT(*) FROM content_assessment").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM canonical_event_evidence").fetchone()[0] == 0
    conn.close()
