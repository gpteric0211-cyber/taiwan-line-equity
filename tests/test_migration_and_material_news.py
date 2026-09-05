from __future__ import annotations
from contextlib import closing
import sqlite3
from pathlib import Path
import pytest
from core.material_news_schema import archive_material_news
from repository.news_radar_repository import prune_news_radar_events
from repository.market_microstructure_repository import read_active_stock_master_rows
from tools.migrate_legacy import snapshot


def test_snapshot_closes_handles_and_handles_non_ascii_and_fragment_paths(tmp_path):
    source = tmp_path / "舊資料 # source.db"
    target = tmp_path / "新專案 空間" / "market.db"
    with closing(sqlite3.connect(source)) as conn, conn:
        conn.execute("CREATE TABLE prices(code TEXT PRIMARY KEY,close REAL)")
        conn.execute("INSERT INTO prices VALUES('2330',100)")
    report = snapshot(source, target)
    assert report["integrity_check"] == ["ok"]
    assert report["table_rows"] == {"prices": 1}
    renamed = target.with_name("renamed.db")
    target.rename(renamed)  # Windows fails if snapshot leaked a SQLite handle.
    with closing(sqlite3.connect(source)) as conn:
        assert conn.execute("SELECT close FROM prices").fetchone()[0] == 100
    with pytest.raises(FileExistsError):
        snapshot(source, renamed)


def test_expired_material_news_is_archived_before_rolling_cleanup():
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute(
            "CREATE TABLE news_radar_event(event_key TEXT PRIMARY KEY,event_date TEXT,published_at TEXT,title TEXT,publisher TEXT,source_url TEXT,source_id TEXT,verification_status TEXT)"
        )
        rows = [
            (
                "important",
                "2020-01-01",
                "2020-01-01T10:00:00",
                "緯穎除權公告",
                "source",
                "https://example.invalid/1",
                "test",
                "unverified",
            ),
            (
                "ordinary",
                "2020-01-01",
                "2020-01-01T10:00:00",
                "市場今日交投概況",
                "source",
                "https://example.invalid/2",
                "test",
                "unverified",
            ),
        ]
        conn.executemany("INSERT INTO news_radar_event VALUES(?,?,?,?,?,?,?,?)", rows)
        assert prune_news_radar_events(conn) == 2
        assert conn.execute("SELECT COUNT(*) FROM news_radar_event").fetchone()[0] == 0
        assert conn.execute("SELECT event_key,verification_status FROM material_news_archive").fetchall() == [
            ("important", "unverified")
        ]
        archive_material_news(conn)
        assert conn.execute("SELECT COUNT(*) FROM material_news_archive").fetchone()[0] == 1


def test_registry_reader_uses_supplied_connection_without_committing(monkeypatch):
    from repository import market_microstructure_repository as repo

    monkeypatch.setattr(repo, "read_only_db", lambda: pytest.fail("opened unrelated global DB"))
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE stock_master(code TEXT,name TEXT,market TEXT,exchange TEXT,is_active INTEGER,security_type TEXT)"
        )
        conn.execute("INSERT INTO stock_master VALUES('2330','台積電','listed','TWSE',1,'stock')")
        assert conn.in_transaction
        assert read_active_stock_master_rows(conn)[0]["code"] == "2330"
        assert conn.in_transaction


def test_official_disclosures_and_licensed_material_metadata_survive_rolling_cleanup():
    from core.official_event_schema import ensure_official_event_schema
    from repository.official_event_repository import prune_official_events
    from repository.external_event_repository import prune_external_market_events

    with closing(sqlite3.connect(":memory:")) as conn:
        ensure_official_event_schema(conn)
        for key, day in [("same", "2010-01-01"), ("newer", "2026-01-01")]:
            conn.execute(
                "INSERT INTO official_company_event(event_key,disclosed_date,disclosed_time,code,company_name,subject,market,source,source_quality,fetched_at) VALUES(?,?, '120000','6669','緯穎','公告除權基準日','listed','TWSE','official',?)",
                (key, day, day),
            )
        conn.execute(
            "CREATE TABLE external_market_event(event_key TEXT,event_date TEXT,published_at TEXT,title TEXT,publisher TEXT,source_url TEXT,source_id TEXT,source_quality TEXT)"
        )
        conn.executemany(
            "INSERT INTO external_market_event VALUES(?,'2010-01-01',NULL,?,'licensed','https://example.invalid/news','feed','licensed')",
            [("same", "公司股票分割公告"), ("ordinary", "市場交投概況")],
        )
        assert prune_official_events(conn, retain_days=1) == 1
        assert prune_external_market_events(conn) == 2
        records = dict(conn.execute("SELECT event_key,verification_status FROM material_news_archive"))
        assert records == {
            "official:same": "official_source",
            "official:newer": "official_source",
            "external:same": "unverified",
        }
        assert conn.execute("SELECT COUNT(*) FROM official_company_event").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM external_market_event").fetchone()[0] == 0
