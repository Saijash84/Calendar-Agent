# database.py

import sqlite3
from datetime import datetime

DB_FILE = "bookings.db"

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary TEXT,
                event_id TEXT,
                start_time TEXT,
                end_time TEXT,
                timezone TEXT,
                status TEXT DEFAULT 'active', -- 'active', 'cancelled'
                created_at TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()

def save_booking(summary, event_id, start_time, end_time, timezone):
    now = datetime.utcnow().isoformat()
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO bookings (summary, event_id, start_time, end_time, timezone, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
        """, (summary, event_id, start_time, end_time, timezone, now, now))
        conn.commit()

def list_bookings(status='active'):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, summary, event_id, start_time, end_time, timezone, status
            FROM bookings
            WHERE status = ?
            ORDER BY start_time ASC
        """, (status,))
        return cursor.fetchall()

def get_booking_by_id(booking_id):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, summary, event_id, start_time, end_time, timezone, status
            FROM bookings WHERE id = ?
        """, (booking_id,))
        return cursor.fetchone()

def get_last_booking():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, summary, event_id, start_time, end_time, timezone, status
            FROM bookings
            WHERE status = 'active'
            ORDER BY id DESC LIMIT 1
        """)
        return cursor.fetchone()

def cancel_booking(booking_id):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE bookings SET status = 'cancelled', updated_at = ?
            WHERE id = ? AND status = 'active'
        """, (datetime.utcnow().isoformat(), booking_id))
        conn.commit()

def update_booking(booking_id, summary, start_time, end_time, timezone):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE bookings
            SET summary = ?, start_time = ?, end_time = ?, timezone = ?, updated_at = ?
            WHERE id = ? AND status = 'active'
        """, (summary, start_time, end_time, timezone, datetime.utcnow().isoformat(), booking_id))
        conn.commit()
