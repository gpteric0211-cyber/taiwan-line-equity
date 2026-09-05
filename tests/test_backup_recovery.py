from contextlib import closing
import json
import sqlite3
import pytest
from tools.migrate_legacy import snapshot
from tools.verify_backup import verify


def prepared(tmp_path):
    original = tmp_path / "source.sqlite3"
    with closing(sqlite3.connect(original)) as conn, conn:
        conn.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO sample VALUES(1,'retained')")
    backup = tmp_path / "backup"
    record = snapshot(original, backup / "market.sqlite3")
    (backup / "manifest.json").write_text(json.dumps({"market": record}))
    return backup


def test_backup_can_be_restored_without_original_directory(tmp_path):
    backup = prepared(tmp_path)
    (tmp_path / "source.sqlite3").unlink()
    restored = tmp_path / "another-location" / "restored"
    assert verify(backup, restored)["restore_rehearsed"]
    with closing(sqlite3.connect(restored / "market.sqlite3")) as conn:
        assert conn.execute("SELECT value FROM sample").fetchone()[0] == "retained"
    with pytest.raises(FileExistsError):
        verify(backup, restored)


def test_backup_corruption_is_not_published(tmp_path):
    backup = prepared(tmp_path)
    with (backup / "market.sqlite3").open("ab") as stream:
        stream.write(b"damaged")
    restored = tmp_path / "restored"
    with pytest.raises(ValueError, match="hash mismatch"):
        verify(backup, restored)
    assert not (restored / "market.sqlite3").exists()


def test_foreign_key_violation_blocks_snapshot_publication(tmp_path):
    original = tmp_path / "broken.sqlite3"
    with closing(sqlite3.connect(original)) as conn, conn:
        conn.executescript(
            "CREATE TABLE parent(id INTEGER PRIMARY KEY); CREATE TABLE child(id INTEGER REFERENCES parent(id)); INSERT INTO child VALUES(1);"
        )
    target = tmp_path / "backup" / "market.sqlite3"
    with pytest.raises(RuntimeError, match="foreign-key"):
        snapshot(original, target)
    assert not target.exists()
