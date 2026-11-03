"""Durable feedback outbox; delivery uses the private Google Apps Script relay."""
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)


class FeedbackOutbox:
    def __init__(self, path, url="", secret=""):
        self.path = Path(path)
        self.url = url
        self.secret = secret
        self._delivery_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    @property
    def configured(self):
        parsed = urlparse(self.url)
        return bool(self.secret and parsed.scheme == "https" and
                    parsed.netloc == "script.google.com" and
                    parsed.path.startswith("/macros/s/") and parsed.path.endswith("/exec"))

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=15)
        self.path.chmod(0o600)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, sent_at REAL, last_error TEXT
            )""")
            with conn:
                yield conn
        finally:
            conn.close()

    def enqueue(self, entry):
        # Commit the full, opt-in-only payload before acknowledging the submission.
        with self._connect() as conn:
            conn.execute("INSERT INTO feedback (id, payload) VALUES (?, ?)",
                         (entry["id"], json.dumps(entry)))
        self._wake.set()

    def deliver_pending(self, limit=10):
        if not self.configured or not self._delivery_lock.acquire(blocking=False):
            return 0
        sent = 0
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT * FROM feedback WHERE sent_at IS NULL AND next_attempt <= ? "
                                    "ORDER BY rowid LIMIT ?", (time.time(), limit)).fetchall()
            for row in rows:
                if self._stop.is_set():
                    break
                try:
                    response = requests.post(self.url, json={"secret": self.secret,
                                             "feedback": json.loads(row["payload"])}, timeout=(5, 25))
                    response.raise_for_status()
                    result = response.json()
                    if result.get("ok") is not True or result.get("id") != row["id"]:
                        raise ValueError("Relay did not acknowledge this feedback")
                except Exception as exc:
                    # Never log feedback, addresses, credentials, response bodies or URLs.
                    error = type(exc).__name__
                    delay = min(3600, 60 * 2 ** min(row["attempts"], 6))
                    with self._connect() as conn:
                        conn.execute("UPDATE feedback SET attempts=attempts+1, next_attempt=?, last_error=? WHERE id=?",
                                     (time.time() + delay, error, row["id"]))
                    log.warning("Feedback email %s deferred (%s)", row["id"], error)
                else:
                    with self._connect() as conn:
                        conn.execute("UPDATE feedback SET sent_at=?, attempts=attempts+1, last_error=NULL WHERE id=?",
                                     (time.time(), row["id"]))
                    sent += 1
            return sent
        finally:
            self._delivery_lock.release()

    def start(self):
        if not self.configured:
            log.warning("Feedback email relay is not configured; submissions will be saved for later delivery")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="feedback-mail", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            self._wake.clear()
            try:
                self.deliver_pending()
            except Exception:
                log.error("Feedback email queue unavailable; will retry")
            self._wake.wait(30)

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=35)


def from_environment():
    return FeedbackOutbox(
        os.environ.get("FEEDBACK_DB_PATH", Path(__file__).parent / "data" / "feedback-mail.db"),
        os.environ.get("FEEDBACK_MAIL_URL", ""), os.environ.get("FEEDBACK_MAIL_SECRET", ""))
