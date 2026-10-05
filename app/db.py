"""Per-student saved data (favorites, courses taken, preferences) in SQLite."""
import json
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "qurated.db"
_lock = threading.Lock()  # FastAPI runs sync endpoints in a thread pool


def _connect():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT, created_at INTEGER, last_seen INTEGER);
        CREATE TABLE IF NOT EXISTS favorites (email TEXT, code TEXT, card_json TEXT, added_at INTEGER,
                                              PRIMARY KEY (email, code));
        CREATE TABLE IF NOT EXISTS taken (email TEXT, code TEXT, added_at INTEGER, PRIMARY KEY (email, code));
        CREATE TABLE IF NOT EXISTS prefs (email TEXT PRIMARY KEY, json TEXT, updated_at INTEGER);
    """)
    return conn


_conn = _connect()


def _exec(sql, params=()):
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur.fetchall()


def touch_user(email, name):
    now = int(time.time())
    _exec("INSERT INTO users VALUES (?, ?, ?, ?) ON CONFLICT(email) DO UPDATE SET name=excluded.name, last_seen=excluded.last_seen",
          (email, name, now, now))


def favorites(email):
    return [json.loads(r[0]) for r in _exec("SELECT card_json FROM favorites WHERE email=? ORDER BY added_at", (email,))]


def add_favorite(email, code, card):
    _exec("INSERT INTO favorites VALUES (?, ?, ?, ?) ON CONFLICT(email, code) DO UPDATE SET card_json=excluded.card_json",
          (email, code, json.dumps(card), int(time.time())))


def remove_favorite(email, code):
    _exec("DELETE FROM favorites WHERE email=? AND code=?", (email, code))


def taken(email):
    return [r[0] for r in _exec("SELECT code FROM taken WHERE email=? ORDER BY added_at", (email,))]


def add_taken(email, code):
    _exec("INSERT OR IGNORE INTO taken VALUES (?, ?, ?)", (email, code, int(time.time())))


def remove_taken(email, code):
    _exec("DELETE FROM taken WHERE email=? AND code=?", (email, code))


def prefs(email):
    rows = _exec("SELECT json FROM prefs WHERE email=?", (email,))
    return json.loads(rows[0][0]) if rows else {}


def set_prefs(email, data):
    _exec("INSERT INTO prefs VALUES (?, ?, ?) ON CONFLICT(email) DO UPDATE SET json=excluded.json, updated_at=excluded.updated_at",
          (email, json.dumps(data), int(time.time())))
