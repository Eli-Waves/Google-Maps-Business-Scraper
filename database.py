"""
SQLite database for leads management
"""
import sqlite3
import json
from datetime import datetime

DB_FILE = "leads.db"


def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT UNIQUE,
            website TEXT,
            category TEXT,
            address TEXT,
            maps_url TEXT,
            status TEXT DEFAULT 'new',
            conversation TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()
    print("[✓] Database initialized")


def upsert_lead(lead: dict):
    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO leads (name, phone, website, category, address, maps_url)
            VALUES (:name, :phone, :website, :category, :address, :maps_url)
            ON CONFLICT(phone) DO NOTHING
        """, lead)
        conn.commit()
    finally:
        conn.close()


def get_new_leads():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM leads WHERE status = 'new' AND phone IS NOT NULL").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_lead_by_phone(phone: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM leads WHERE phone = ?", (phone,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_lead_status(phone: str, status: str):
    conn = get_conn()
    conn.execute(
        "UPDATE leads SET status = ?, updated_at = ? WHERE phone = ?",
        (status, datetime.now().isoformat(), phone)
    )
    conn.commit()
    conn.close()


def append_message(phone: str, role: str, content: str):
    """Append a message to the lead's conversation history."""
    conn = get_conn()
    row = conn.execute("SELECT conversation FROM leads WHERE phone = ?", (phone,)).fetchone()
    if not row:
        conn.close()
        return
    history = json.loads(row["conversation"])
    history.append({"role": role, "content": content, "time": datetime.now().isoformat()})
    conn.execute(
        "UPDATE leads SET conversation = ?, updated_at = ? WHERE phone = ?",
        (json.dumps(history), datetime.now().isoformat(), phone)
    )
    conn.commit()
    conn.close()


def get_conversation(phone: str) -> list:
    conn = get_conn()
    row = conn.execute("SELECT conversation FROM leads WHERE phone = ?", (phone,)).fetchone()
    conn.close()
    if not row:
        return []
    return json.loads(row["conversation"])
