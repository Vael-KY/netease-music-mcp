"""SQLite state for the companion bridge: what was forwarded, what to deliver back."""
import json
import os
import sqlite3
import threading
import time


class Store:
    def __init__(self, path):
        if path != ':memory:':
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock, self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
                -- events waiting to reach the companion relay (relay_id NULL = not yet accepted)
                CREATE TABLE IF NOT EXISTS inbox (key TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    relay_id INTEGER, created REAL NOT NULL);
                -- replies to say in the room; sent_parts lets a restart resume mid-reply
                CREATE TABLE IF NOT EXISTS outbox (message_id INTEGER PRIMARY KEY, text TEXT NOT NULL,
                    room_id TEXT NOT NULL, state TEXT NOT NULL, sent_parts INTEGER NOT NULL DEFAULT 0,
                    detail TEXT, created REAL NOT NULL);
            """)

    # -- cursor / small values --
    def get(self, key, default=None):
        with self.lock:
            row = self.conn.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default

    def put(self, key, value):
        with self.lock, self.conn:
            self.conn.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', (key, str(value)))

    # -- inbound events --
    def add_event(self, key, payload):
        """Returns False if this event key was already stored (duplicate)."""
        with self.lock, self.conn:
            cur = self.conn.execute('INSERT OR IGNORE INTO inbox VALUES (?,?,NULL,?)',
                                    (key, json.dumps(payload, ensure_ascii=False), time.time()))
        return cur.rowcount == 1

    def pending_events(self, limit=20):
        with self.lock:
            rows = self.conn.execute('SELECT * FROM inbox WHERE relay_id IS NULL ORDER BY created LIMIT ?',
                                     (limit,)).fetchall()
        return [(r['key'], json.loads(r['payload'])) for r in rows]

    def mark_forwarded(self, key, relay_id):
        with self.lock, self.conn:
            self.conn.execute('UPDATE inbox SET relay_id=? WHERE key=?', (int(relay_id), key))

    def drop_stale_events(self, older_than_s):
        """Events that never reached the relay in time are no longer worth saying."""
        with self.lock, self.conn:
            self.conn.execute('DELETE FROM inbox WHERE relay_id IS NULL AND created < ?', (time.time() - older_than_s,))

    def is_our_ingress(self, relay_id):
        with self.lock:
            return self.conn.execute('SELECT 1 FROM inbox WHERE relay_id=?', (int(relay_id),)).fetchone() is not None

    # -- outbound replies --
    def add_reply(self, message_id, text, room_id):
        with self.lock, self.conn:
            self.conn.execute("INSERT OR IGNORE INTO outbox VALUES (?,?,?,'pending',0,NULL,?)",
                              (int(message_id), text, room_id, time.time()))

    def next_reply(self):
        with self.lock:
            row = self.conn.execute("SELECT * FROM outbox WHERE state='pending' ORDER BY message_id LIMIT 1").fetchone()
        return dict(row) if row else None

    def reply_progress(self, message_id, sent_parts):
        with self.lock, self.conn:
            self.conn.execute('UPDATE outbox SET sent_parts=? WHERE message_id=?', (sent_parts, message_id))

    def finish_reply(self, message_id, state, detail=None):
        with self.lock, self.conn:
            self.conn.execute('UPDATE outbox SET state=?, detail=? WHERE message_id=?', (state, detail, message_id))

    def reply_counts(self):
        with self.lock:
            rows = self.conn.execute('SELECT state, COUNT(*) n FROM outbox GROUP BY state').fetchall()
        return {r['state']: r['n'] for r in rows}
