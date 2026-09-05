"""One market DB for web/Bot; LINE secrets must not select a second DB."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from core import config, db as web_db, line_bot_config
from repository import market_microstructure_repository as bot_db
from scripts import start_line_bot_stack as launcher


@pytest.fixture
def layout(tmp_path, monkeypatch):
    root = tmp_path / "moved project" / "review_src"
    root.mkdir(parents=True)
    private = root.parent / ".env.line_bot"
    private.write_text("", encoding="utf-8")
    # Register restoration even when the original process had no DB override.
    monkeypatch.setenv("TAIWAN50_DB_PATH", "")
    monkeypatch.delenv("TAIWAN50_DB_PATH")
    monkeypatch.setattr(config, "ROOT", root)
    monkeypatch.setattr(config, "DATA_DIR", root / "data")
    monkeypatch.setattr(bot_db, "REVIEW_SRC", root)
    monkeypatch.setattr(line_bot_config, "REVIEW_SRC", root)
    monkeypatch.setattr(line_bot_config, "LINE_BOT_ENV_FILE", private)
    monkeypatch.setattr(launcher, "REVIEW_SRC", root)
    monkeypatch.setattr(launcher, "PRIVATE_ENV", private)
    monkeypatch.chdir(tmp_path)  # Paths must not depend on launcher cwd.
    return root, private


@pytest.mark.parametrize("selected", ["data/common.sqlite3", "'data/space name.db'", ""])
def test_both_readers_resolve_shared_env_without_import_order_dependency(layout, selected):
    root, _ = layout
    (root / ".env").write_text(f"TAIWAN50_DB_PATH={selected}\n", encoding="utf-8")
    relative = selected.strip("'") or "data/taiwan50.db"
    expected = (root / relative).resolve()
    assert bot_db._database_path().resolve() == expected
    assert config.resolve_db_path().resolve() == expected
    assert not expected.exists()


@pytest.mark.parametrize("explicit", ["data/override.db", "absolute", ""])
def test_explicit_environment_has_same_precedence(layout, monkeypatch, explicit):
    root, _ = layout
    (root / ".env").write_text("TAIWAN50_DB_PATH=data/common.db\n", encoding="utf-8")
    value = str(root.parent / "absolute.db") if explicit == "absolute" else explicit
    monkeypatch.setenv("TAIWAN50_DB_PATH", value)
    expected = Path(value) if explicit == "absolute" else root / (value or "data/taiwan50.db")
    assert bot_db._database_path().resolve() == expected.resolve()
    assert config.resolve_db_path().resolve() == expected.resolve()


def load_entry(entry):
    if entry == "launcher":
        launcher._load_configuration()
    elif entry == "bot":
        line_bot_config.load_line_bot_env(allowed_names={"TAIWAN50_DB_PATH"})
    else:
        line_bot_config.load_line_bot_env()


@pytest.mark.parametrize("entry", ["launcher", "bot", "line"])
def test_each_entry_pins_the_shared_database(layout, entry):
    root, _ = layout
    (root / ".env").write_text("TAIWAN50_DB_PATH=data/common.db\n", encoding="utf-8")
    load_entry(entry)
    assert Path(os.environ["TAIWAN50_DB_PATH"]) == (root / "data/common.db").resolve()
    assert config.resolve_db_path().resolve() == bot_db._database_path().resolve()


@pytest.mark.parametrize("entry", ["launcher", "bot", "line"])
def test_private_line_database_conflict_is_rejected_before_loading(layout, entry):
    root, private = layout
    (root / ".env").write_text("TAIWAN50_DB_PATH=data/common.db\n", encoding="utf-8")
    private.write_text("TAIWAN50_DB_PATH=data/different.db\n", encoding="utf-8")
    before = dict(os.environ)
    with pytest.raises(ValueError, match="market_database_configuration_conflict"):
        load_entry(entry)
    assert dict(os.environ) == before
    assert not (root / "data").exists()


@pytest.mark.parametrize("entry", ["launcher", "bot", "line"])
def test_matching_legacy_private_setting_is_compatible(layout, entry):
    root, private = layout
    (root / ".env").write_text("TAIWAN50_DB_PATH=data/common.db\n", encoding="utf-8")
    private.write_text(f"TAIWAN50_DB_PATH={root.as_posix()}/data/common.db\n", encoding="utf-8")
    load_entry(entry)
    assert Path(os.environ["TAIWAN50_DB_PATH"]) == (root / "data/common.db").resolve()


def test_two_sqlite_readers_open_the_same_file_and_reject_writes(layout, monkeypatch):
    root, _ = layout
    path = root / "shared.sqlite3"
    (root / ".env").write_text("TAIWAN50_DB_PATH=shared.sqlite3\n", encoding="utf-8")
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE history_price(code TEXT, date TEXT, close REAL)")
        conn.execute("INSERT INTO history_price VALUES ('2360', '2026-08-28', 2010)")
        conn.commit()
    monkeypatch.setattr(web_db, "DB_PATH", config.resolve_db_path())
    before = path.read_bytes()
    with closing(web_db.read_only_db()) as web, closing(bot_db.read_only_db()) as bot:
        for conn in (web, bot):
            assert Path(conn.execute("PRAGMA database_list").fetchone()[2]).samefile(path)
            assert tuple(conn.execute("SELECT * FROM history_price").fetchone()) == (
                "2360", "2026-08-28", 2010,
            )
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                conn.execute("DELETE FROM history_price")
    assert path.read_bytes() == before


def test_missing_shared_db_does_not_fall_back_or_create_a_second_db(layout):
    root, _ = layout
    (root / ".env").write_text("TAIWAN50_DB_PATH=missing/shared.db\n", encoding="utf-8")
    (root / "data").mkdir()
    default = root / "data/taiwan50.db"
    with closing(sqlite3.connect(default)) as conn:
        conn.execute("CREATE TABLE marker(value TEXT)")
        conn.commit()
    before = default.read_bytes()
    with pytest.raises(sqlite3.OperationalError):
        bot_db.read_only_db()
    assert not (root / "missing").exists()
    assert default.read_bytes() == before


def test_market_selection_does_not_merge_or_change_conversation_db(layout, monkeypatch):
    root, _ = layout
    monkeypatch.setenv("LINE_MEMORY_DB_PATH", "private/conversations.sqlite3")
    line_bot_config.load_line_bot_env()
    assert os.environ["LINE_MEMORY_DB_PATH"] == "private/conversations.sqlite3"
    assert Path(os.environ["TAIWAN50_DB_PATH"]) == root / "data/taiwan50.db"
