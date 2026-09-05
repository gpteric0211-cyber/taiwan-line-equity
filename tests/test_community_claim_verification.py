from __future__ import annotations

from analysis.community_claim_verification import verify_community_claims


def test_community_revenue_claim_is_checked_against_official_metrics():
    result = verify_community_claims(
        posts=[{
            "post_id": "p1",
            "published_at": "2026-08-26T09:10:00+08:00",
            "title": "鴻海營收年增 54.19%，一定會漲",
            "content": "",
            "url": "https://example.invalid/p1",
        }],
        official_events=[{
            "event_date": "2026-08-17",
            "published_at": "2026-08-17T18:00:00+08:00",
            "publisher": "臺灣證券交易所",
            "title": "鴻海 2026-07 月營收",
            "summary_excerpt": "官方月營收",
            "metrics": {"year_over_year_pct": 54.189},
            "source_quality": "official",
            "quality_status": "ok",
        }],
        quote={"price": 247, "previous_close": 243, "open": 243, "high": 248, "low": 241.5},
        reference_date="2026-08-26",
    )
    claim = result["results"][0]
    assert claim["status"] == "facts_verified_prediction_unverified"
    assert claim["community_repetition_counts_as_independent_confirmation"] is False
    assert result["price_reaction"]["change_pct"] == 1.6461
    assert result["price_reaction"]["causality_proven"] is False
    assert result["can_override_main_status"] is False


def test_repeated_unverified_posts_do_not_become_true():
    posts = [
        {"post_id": str(index), "published_at": "2026-08-26T10:00:00+08:00", "title": "鴻海拿到神秘訂單", "content": "", "url": f"https://example.invalid/{index}"}
        for index in range(3)
    ]
    result = verify_community_claims(
        posts=posts,
        official_events=[],
        quote={},
        reference_date="2026-08-26",
    )
    assert all(row["status"] == "unverified" for row in result["results"])
    assert all(row["community_repeat_count"] == 3 for row in result["results"])
