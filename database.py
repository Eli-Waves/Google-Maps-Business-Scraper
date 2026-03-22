"""
Database layer — PostgreSQL (permanent) with SQLite fallback
"""
import os
import json
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    if DATABASE_URL:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL)
        return conn, "pg"
    else:
        import sqlite3
        conn = sqlite3.connect("leads.db")
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"


def init_db():
    conn, db = get_conn()
    cur = conn.cursor()
    if db == "pg":
        cur.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id SERIAL PRIMARY KEY,
                name TEXT,
                phone TEXT UNIQUE,
                website TEXT,
                category TEXT,
                address TEXT,
                maps_url TEXT,
                status TEXT DEFAULT 'new',
                conversation TEXT DEFAULT '[]',
                deal_amount INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
    else:
        cur.execute("""
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
                deal_amount INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
    conn.commit()
    cur.close()
    conn.close()
    print("[✓] Database initialized")


def row_to_dict(row, db, cur=None):
    if db == "pg":
        cols = [desc[0] for desc in cur.description]
        return dict(zip(cols, row))
    else:
        return dict(row)


def upsert_lead(lead: dict):
    conn, db = get_conn()
    cur = conn.cursor()
    try:
        if db == "pg":
            cur.execute("""
                INSERT INTO leads (name, phone, website, category, address, maps_url)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT(phone) DO NOTHING
            """, (lead.get("name"), lead.get("phone"), lead.get("website"),
                  lead.get("category"), lead.get("address"), lead.get("maps_url")))
        else:
            cur.execute("""
                INSERT INTO leads (name, phone, website, category, address, maps_url)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(phone) DO NOTHING
            """, (lead.get("name"), lead.get("phone"), lead.get("website"),
                  lead.get("category"), lead.get("address"), lead.get("maps_url")))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_new_leads():
    conn, db = get_conn()
    cur = conn.cursor()
    if db == "pg":
        cur.execute("SELECT * FROM leads WHERE status = 'new' AND phone IS NOT NULL")
    else:
        cur.execute("SELECT * FROM leads WHERE status = 'new' AND phone IS NOT NULL")
    rows = cur.fetchall()
    result = [row_to_dict(r, db, cur) for r in rows]
    cur.close()
    conn.close()
    return result


def get_lead_by_phone(phone: str):
    conn, db = get_conn()
    cur = conn.cursor()
    if db == "pg":
        cur.execute("SELECT * FROM leads WHERE phone = %s", (phone,))
    else:
        cur.execute("SELECT * FROM leads WHERE phone = ?", (phone,))
    row = cur.fetchone()
    result = row_to_dict(row, db, cur) if row else None
    cur.close()
    conn.close()
    return result


def update_lead_status(phone: str, status: str):
    conn, db = get_conn()
    cur = conn.cursor()
    if db == "pg":
        cur.execute("UPDATE leads SET status = %s, updated_at = NOW() WHERE phone = %s", (status, phone))
    else:
        cur.execute("UPDATE leads SET status = ?, updated_at = ? WHERE phone = ?",
                    (status, datetime.now().isoformat(), phone))
    conn.commit()
    cur.close()
    conn.close()


def update_deal_amount(phone: str, amount: int):
    conn, db = get_conn()
    cur = conn.cursor()
    if db == "pg":
        cur.execute("UPDATE leads SET deal_amount = %s, updated_at = NOW() WHERE phone = %s", (amount, phone))
    else:
        cur.execute("UPDATE leads SET deal_amount = ?, updated_at = ? WHERE phone = ?",
                    (amount, datetime.now().isoformat(), phone))
    conn.commit()
    cur.close()
    conn.close()


def append_message(phone: str, role: str, content: str):
    conn, db = get_conn()
    cur = conn.cursor()
    if db == "pg":
        cur.execute("SELECT conversation FROM leads WHERE phone = %s", (phone,))
    else:
        cur.execute("SELECT conversation FROM leads WHERE phone = ?", (phone,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return
    history = json.loads(row[0] if db == "pg" else row["conversation"])
    history.append({"role": role, "content": content, "time": datetime.now().isoformat()})
    if db == "pg":
        cur.execute("UPDATE leads SET conversation = %s, updated_at = NOW() WHERE phone = %s",
                    (json.dumps(history), phone))
    else:
        cur.execute("UPDATE leads SET conversation = ?, updated_at = ? WHERE phone = ?",
                    (json.dumps(history), datetime.now().isoformat(), phone))
    conn.commit()
    cur.close()
    conn.close()


def get_conversation(phone: str) -> list:
    conn, db = get_conn()
    cur = conn.cursor()
    if db == "pg":
        cur.execute("SELECT conversation FROM leads WHERE phone = %s", (phone,))
    else:
        cur.execute("SELECT conversation FROM leads WHERE phone = ?", (phone,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return []
    return json.loads(row[0] if db == "pg" else row["conversation"])


def get_all_leads():
    conn, db = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM leads ORDER BY updated_at DESC")
    rows = cur.fetchall()
    result = [row_to_dict(r, db, cur) for r in rows]
    cur.close()
    conn.close()
    return result


def get_revenue():
    conn, db = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT SUM(deal_amount) FROM leads WHERE status = 'converted'")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] or 0
