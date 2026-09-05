"""Transactional account-schema upgrades. Never touch the market database."""

SCHEMA_VERSION = 5
STATEMENTS = (
    """CREATE TABLE member_access (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('member','manager','owner')),
        plan TEXT NOT NULL DEFAULT 'free' CHECK(plan IN ('free','monthly','complimentary')),
        expires_at REAL,
        version INTEGER NOT NULL DEFAULT 0,
        CHECK((plan='monthly' AND expires_at IS NOT NULL) OR
              (plan!='monthly' AND expires_at IS NULL)))""",
    """CREATE TABLE member_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_id INTEGER REFERENCES users(id), target_id INTEGER NOT NULL REFERENCES users(id),
        action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
        reason TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE, created_at REAL NOT NULL)""",
    "CREATE INDEX idx_member_audit_target ON member_audit(target_id,id)",
)


def migrate(conn):
    """Serialized, restart-safe DDL; caller owns the connection, not a transaction."""
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT COALESCE(MAX(version),0) FROM account_migration").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise RuntimeError("Account database requires a newer application version")
        if current < 2:
            for statement in STATEMENTS:
                conn.execute(statement)
            conn.execute("INSERT INTO account_migration(version) VALUES(2)")
        if current < 3:
            conn.execute("CREATE TABLE account_security(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, credential_version INTEGER NOT NULL DEFAULT 0)")
            conn.execute("ALTER TABLE email_verifications ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
            conn.execute("INSERT INTO account_migration(version) VALUES(3)")
        if current < 4:
            conn.execute("ALTER TABLE account_security ADD COLUMN phone_required INTEGER NOT NULL DEFAULT 0")
            conn.execute("CREATE TABLE account_phone(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, phone_hash TEXT NOT NULL UNIQUE, encrypted BLOB NOT NULL, verified_at REAL NOT NULL)")
            conn.execute("CREATE TABLE phone_challenge(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, request_id TEXT NOT NULL, phone_hash TEXT NOT NULL, encrypted BLOB NOT NULL, provider_sid TEXT, expires_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)")
            conn.execute("CREATE TABLE phone_send_attempt(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, phone_hash TEXT NOT NULL, created_at REAL NOT NULL)")
            conn.execute("CREATE INDEX idx_phone_attempt_time ON phone_send_attempt(created_at)")
            conn.execute("INSERT INTO account_migration(version) VALUES(4)")
        if current < 5:
            conn.execute("CREATE TABLE member_principal(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, portfolio_subject TEXT NOT NULL UNIQUE)")
            conn.execute("INSERT INTO account_migration(version) VALUES(5)")
