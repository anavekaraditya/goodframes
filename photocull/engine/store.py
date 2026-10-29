"""Per-shoot SQLite store: technical metrics, Claude reviews and the user's overrides.

Lives at <folder>/.photo-cull/cull.db. Photos are keyed by a content fingerprint so
renamed files keep their reviews and edited files get re-analysed.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time

WORK_DIR = ".photo-cull"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    fp TEXT PRIMARY KEY, file TEXT NOT NULL, metrics_version INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews (
    fp TEXT PRIMARY KEY, data TEXT NOT NULL, model TEXT, rubric_version INTEGER, reviewed_at REAL);
CREATE TABLE IF NOT EXISTS overrides (
    fp TEXT PRIMARY KEY, action TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def fingerprint(path):
    """Size + mtime + hash of the first 64 KB: cheap, and changes when the file is edited."""
    st = os.stat(path)
    h = hashlib.sha1(f"{st.st_size}:{st.st_mtime_ns}".encode())
    with open(path, "rb") as f:
        h.update(f.read(65536))
    return h.hexdigest()[:16]


class Store:
    def __init__(self, folder):
        self.folder = folder
        self.work = os.path.join(folder, WORK_DIR)
        os.makedirs(os.path.join(self.work, "thumbs"), exist_ok=True)
        self._db = sqlite3.connect(os.path.join(self.work, "cull.db"), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def thumb_path(self, fp):
        return os.path.join(self.work, "thumbs", fp + ".jpg")

    # photos
    def get_photo(self, fp, metrics_version):
        with self._lock:
            row = self._db.execute(
                "SELECT data FROM photos WHERE fp=? AND metrics_version=?", (fp, metrics_version)).fetchone()
        return json.loads(row[0]) if row else None

    def put_photo(self, fp, file, metrics_version, data):
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO photos VALUES (?,?,?,?)",
                             (fp, file, metrics_version, json.dumps(data)))
            self._db.commit()

    # reviews
    def reviews(self):
        with self._lock:
            rows = self._db.execute("SELECT fp, data, model, rubric_version FROM reviews").fetchall()
        return {fp: dict(json.loads(d), model=m, rubric_version=rv) for fp, d, m, rv in rows}

    def put_reviews(self, items, model, rubric_version):
        """items: {fp: review dict}"""
        now = time.time()
        with self._lock:
            self._db.executemany("INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?)",
                                 [(fp, json.dumps(r), model, rubric_version, now) for fp, r in items.items()])
            self._db.commit()

    def clear_reviews(self):
        with self._lock:
            self._db.execute("DELETE FROM reviews")
            self._db.commit()

    # overrides: pick | unpick | gem | ungem
    def overrides(self):
        with self._lock:
            return dict(self._db.execute("SELECT fp, action FROM overrides").fetchall())

    def set_override(self, fp, action):
        with self._lock:
            if action is None:
                self._db.execute("DELETE FROM overrides WHERE fp=?", (fp,))
            else:
                self._db.execute("INSERT OR REPLACE INTO overrides VALUES (?,?,?)", (fp, action, time.time()))
            self._db.commit()

    # meta
    def get_meta(self, key, default=None):
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))
            self._db.commit()
