import sqlite3
import json
from contextlib import contextmanager

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    title TEXT,
    company TEXT,
    location TEXT,
    work_model TEXT,
    salary TEXT,
    grad_time TEXT,
    company_size TEXT,
    company_industry TEXT,
    qualifications TEXT,
    apply_url TEXT,
    date_posted TEXT,
    first_seen_at TEXT,
    fit_score INTEGER,
    fit_reasoning TEXT,
    status TEXT DEFAULT 'new',
    resume_bullets TEXT,
    cover_letter TEXT,
    scored_at TEXT,
    drafted_at TEXT,
    status_updated_at TEXT,
    category TEXT,
    filter_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_fit_score ON jobs(fit_score);
"""

# Additive migrations for databases created before these columns existed.
MIGRATIONS = {
    "category": "ALTER TABLE jobs ADD COLUMN category TEXT",
    "filter_reason": "ALTER TABLE jobs ADD COLUMN filter_reason TEXT",
}


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
        for col, ddl in MIGRATIONS.items():
            if col not in existing:
                conn.execute(ddl)
        # Rows from before multi-source all came from the ML/AI Airtable view.
        conn.execute("UPDATE jobs SET category = 'Machine Learning & AI' WHERE category IS NULL")


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if d.get("resume_bullets"):
        try:
            d["resume_bullets"] = json.loads(d["resume_bullets"])
        except (TypeError, ValueError):
            pass
    return d
