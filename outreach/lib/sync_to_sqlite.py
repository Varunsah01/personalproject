#!/usr/bin/env python3
"""Sync tracker.csv → pipeline.db (SQLite).

Runs every 60s via cron. Idempotent — safe to re-run at any time.
Also supports --watch mode for development (loops with 60s sleep).

Usage:
    python outreach/lib/sync_to_sqlite.py           # one-shot
    python outreach/lib/sync_to_sqlite.py --watch    # loop every 60s
"""

from __future__ import annotations

import dataclasses
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from outreach.lib import tracker  # noqa: E402

DB_PATH = _PROJECT_ROOT / "outreach" / "data" / "pipeline.db"
TRACKER_PATH = _PROJECT_ROOT / "outreach" / "data" / "tracker.csv"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS outreach_rows (
    id              TEXT PRIMARY KEY,
    company         TEXT NOT NULL DEFAULT '',
    role_url        TEXT NOT NULL DEFAULT '',
    role_title      TEXT NOT NULL DEFAULT '',
    role_tier       TEXT NOT NULL DEFAULT '',
    person_name     TEXT NOT NULL DEFAULT '',
    person_title    TEXT NOT NULL DEFAULT '',
    person_linkedin TEXT NOT NULL DEFAULT '',
    person_country  TEXT NOT NULL DEFAULT '',
    relationship_type TEXT NOT NULL DEFAULT '',
    email           TEXT NOT NULL DEFAULT '',
    email_confidence TEXT NOT NULL DEFAULT '',
    linkedin_only   TEXT NOT NULL DEFAULT '',
    hook            TEXT NOT NULL DEFAULT '',
    subject         TEXT NOT NULL DEFAULT '',
    body_path       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT '',
    assigned_inbox  TEXT NOT NULL DEFAULT '',
    send_at_utc     TEXT NOT NULL DEFAULT '',
    sent_at_utc     TEXT NOT NULL DEFAULT '',
    replied         TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    last_updated    TEXT NOT NULL DEFAULT '',
    message_id      TEXT NOT NULL DEFAULT '',
    synced_at       TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_outreach_status ON outreach_rows(status);
CREATE INDEX IF NOT EXISTS idx_outreach_company ON outreach_rows(company);
CREATE INDEX IF NOT EXISTS idx_outreach_tier ON outreach_rows(role_tier);

CREATE TABLE IF NOT EXISTS manager_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id      TEXT NOT NULL,
    action      TEXT NOT NULL CHECK(action IN ('approve', 'reject')),
    reason      TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    decided_at  TEXT NOT NULL DEFAULT (datetime('now')),
    synced_to_csv INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_decisions_row ON manager_decisions(row_id);

CREATE TABLE IF NOT EXISTS agent_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    agent       TEXT NOT NULL,
    action      TEXT NOT NULL DEFAULT '',
    target      TEXT NOT NULL DEFAULT '',
    message     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL CHECK(status IN ('ok', 'warn', 'error')),
    row_id      TEXT DEFAULT NULL,
    run_date    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON agent_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_logs_agent ON agent_logs(agent);
CREATE INDEX IF NOT EXISTS idx_logs_status ON agent_logs(status);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    """Open (or create) the SQLite database with schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA_SQL)

    # Migrate: add message_id column if missing (added in follow-up cadence)
    cursor = conn.execute("PRAGMA table_info(outreach_rows)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    if "message_id" not in existing_cols:
        conn.execute(
            "ALTER TABLE outreach_rows ADD COLUMN message_id TEXT NOT NULL DEFAULT ''"
        )

    return conn


def sync_tracker(conn: sqlite3.Connection) -> int:
    """Sync all tracker.csv rows into outreach_rows. Returns row count."""
    rows = tracker.read_all(TRACKER_PATH)
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        values = (*dataclasses.astuple(row), now)
        conn.execute(
            """INSERT OR REPLACE INTO outreach_rows
               (id, company, role_url, role_title, role_tier,
                person_name, person_title, person_linkedin, person_country,
                relationship_type, email, email_confidence, linkedin_only,
                hook, subject, body_path, status, assigned_inbox,
                send_at_utc, sent_at_utc, replied, notes, last_updated,
                message_id, synced_at)
               VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?)""",
            values,
        )

    # Remove rows from SQLite that no longer exist in CSV
    csv_ids = {row.id for row in rows}
    db_ids_cursor = conn.execute("SELECT id FROM outreach_rows")
    db_ids = {r[0] for r in db_ids_cursor.fetchall()}
    stale_ids = db_ids - csv_ids
    if stale_ids:
        conn.executemany(
            "DELETE FROM outreach_rows WHERE id = ?",
            [(sid,) for sid in stale_ids],
        )

    conn.commit()
    return len(rows)


def main() -> None:
    watch = "--watch" in sys.argv

    while True:
        try:
            conn = init_db(DB_PATH)
            count = sync_tracker(conn)
            conn.close()
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] synced {count} rows to {DB_PATH.name}")
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)

        if not watch:
            break
        time.sleep(60)


if __name__ == "__main__":
    main()
