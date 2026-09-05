"""Atomic schema operations that preserve the caller's existing transaction."""

from contextlib import contextmanager
import secrets
import sqlite3


@contextmanager
def schema_transaction(conn: sqlite3.Connection):
    name = "schema_" + secrets.token_hex(8)
    conn.execute("SAVEPOINT " + name)
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO " + name)
        conn.execute("RELEASE " + name)
        raise
    else:
        conn.execute("RELEASE " + name)


def execute_schema_script(conn: sqlite3.Connection, script: str) -> None:
    """Execute trusted schema SQL without executescript's implicit commit."""
    pending = ""
    for fragment in script.split(";"):
        pending += fragment + ";"
        if sqlite3.complete_statement(pending):
            conn.execute(pending)
            pending = ""
    if pending.strip():
        conn.execute(pending)
