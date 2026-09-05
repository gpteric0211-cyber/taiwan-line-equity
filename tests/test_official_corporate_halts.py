from __future__ import annotations
import pytest
from adapter import official_corporate_halts as adapter


def report(fields, rows):
    return {"stat": "OK", "fields": fields, "data": rows}


def test_halts_use_explicit_dates_and_exclude_resumption_day(monkeypatch):
    calls = []

    def fake_get(path, timeout, **params):
        calls.append((path, params))
        if path == adapter.DETAIL_PATH:
            return report(["股票代號：", "股票名稱：", "停止買賣日期："], [["1563  ", "巧新", "115/08/27"]])
        if path.endswith("TWTAUU"):
            return report(
                ["股票代號", "名稱", "恢復買賣日期", "詳細資料"],
                [["1563", "巧新", "115/09/07", "1563  ,20260826"]],
            )
        return report(
            ["股票代號", "名稱", "停止買賣日期", "恢復買賣日期"], [["6949", "測試", "115/08/27", "115/09/07"]]
        )

    monkeypatch.setattr(adapter, "_get", fake_get)
    result = adapter.fetch_corporate_halts(as_of_date="2026-09-04")
    assert result["ok"]
    assert len(result["items"]) == 3
    for row in result["items"]:
        assert row["effective_from"] == "2026-08-27"
        assert row["effective_to"] == "2026-09-06"
        assert row["announcement_date"] == "2026-09-04"
        assert row["source_quality"] == "official"
        assert not {"close", "volume", "open"}.intersection(row)
    assert calls[-1] == (adapter.DETAIL_PATH, {"STK_NO": "1563", "FILE_DATE": "20260826"})


@pytest.mark.parametrize(
    "row",
    [
        {"股票代號": "1563", "停止買賣日期": "", "恢復買賣日期": "115/09/07"},
        {"股票代號": "1563", "停止買賣日期": "115/09/07", "恢復買賣日期": "115/09/07"},
        {"股票代號": "1563", "停止買賣日期": "115/09/08", "恢復買賣日期": "115/09/07"},
    ],
)
def test_missing_or_invalid_interval_cannot_be_inferred(row):
    with pytest.raises(ValueError, match="explicit valid interval"):
        adapter._normalize(row, "twse_capital_reduction", "減資", "2026-09-04")


def test_future_stop_is_not_moved_back_to_observation_date():
    row = adapter._normalize(
        {"股票代號": "1441", "停止買賣日期": "115/09/17", "恢復買賣日期": "115/09/29"},
        "twse_capital_reduction",
        "減資",
        "2026-09-04",
    )
    assert row["effective_from"] == "2026-09-17"
    assert row["effective_to"] == "2026-09-28"


def test_missing_detail_fails_source_without_guessing_halt(monkeypatch):
    def fake_get(path, timeout, **params):
        if path.endswith("TWTAUU"):
            return report(
                ["股票代號", "名稱", "恢復買賣日期", "詳細資料"],
                [["1563", "巧新", "115/09/07", "1563,20260826"]],
            )
        if path == adapter.DETAIL_PATH:
            return report(["股票代號", "股票名稱", "停止買賣日期"], [["9999", "錯誤", "115/08/27"]])
        return report(["股票代號", "名稱", "停止買賣日期", "恢復買賣日期"], [])

    monkeypatch.setattr(adapter, "_get", fake_get)
    result = adapter.fetch_corporate_halts(as_of_date="2026-09-04")
    assert not result["ok"]
    assert result["items"] == []
    assert result["sources"][-1]["status"] == "failed"


@pytest.mark.parametrize(
    "payload", [{"stat": "OK", "data": []}, {"stat": "沒有資料"}, report(["股票代號"], [["1563", "extra"]])]
)
def test_schema_changes_are_reported_not_silently_empty(payload):
    with pytest.raises(ValueError):
        adapter._rows(payload, {"股票代號"})


def test_corporate_halt_metadata_survives_source_deletion():
    import sqlite3
    from core.material_news_schema import archive_material_news
    from repository.trading_restriction_repository import upsert_trading_restrictions

    with sqlite3.connect(":memory:") as conn:
        row = adapter._normalize(
            {"股票代號": "1563", "名稱": "巧新", "停止買賣日期": "115/08/27", "恢復買賣日期": "115/09/07"},
            "twse_capital_resumption",
            "減資恢復買賣公告",
            "2026-09-04",
        )
        upsert_trading_restrictions(conn, [row])
        archive_material_news(conn, {"official_trading_restriction"})
        conn.execute("DELETE FROM official_trading_restriction")
        saved = conn.execute(
            "SELECT title,verification_status,source_url FROM material_news_archive"
        ).fetchone()
        assert "1563" in saved[0] and "2026-08-27" in saved[0]
        assert saved[1] == "official_source"
        assert saved[2].endswith("twtauu.html")
