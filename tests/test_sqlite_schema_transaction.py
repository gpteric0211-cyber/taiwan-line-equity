import sqlite3
import pytest
from core.sqlite_schema import schema_transaction, execute_schema_script

def test_schema_preserves_outer_transaction_and_trigger_semicolons():
    conn=sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE records(value TEXT)")
    conn.execute("INSERT INTO records VALUES('uncommitted')")
    with schema_transaction(conn):
        execute_schema_script(conn,"CREATE TABLE audit(value TEXT); CREATE TRIGGER copy AFTER INSERT ON records BEGIN INSERT INTO audit VALUES(new.value || ';ok'); END;")
    conn.execute("INSERT INTO records VALUES('next')")
    assert conn.execute("SELECT value FROM audit").fetchone()[0]=="next;ok"
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]==0
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='audit'").fetchone()[0]==0

def test_failed_schema_is_atomic_and_keeps_prior_work():
    conn=sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE records(value TEXT)")
    conn.execute("INSERT INTO records VALUES('kept')")
    with pytest.raises(sqlite3.Error), schema_transaction(conn):
        execute_schema_script(conn,"CREATE TABLE partial(value TEXT); INVALID SQL;")
    assert conn.in_transaction
    assert conn.execute("SELECT value FROM records").fetchone()[0]=="kept"
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='partial'").fetchone()[0]==0

def test_standalone_schema_is_committed_for_reopen(tmp_path):
    path=tmp_path/"schema.sqlite3"
    conn=sqlite3.connect(path)
    with schema_transaction(conn):
        execute_schema_script(conn,"CREATE TABLE ready(value TEXT); INSERT INTO ready VALUES('saved');")
    conn.close()
    with sqlite3.connect(path) as reopened:
        assert reopened.execute("SELECT value FROM ready").fetchone()[0]=="saved"
