"""SQLite de-duplication so each job is only posted once."""
import hashlib
import sqlite3
import time
from urllib.parse import urlsplit, urlunsplit


def normalize(url: str) -> str:
    s = urlsplit(url.strip())
    return urlunsplit((s.scheme.lower(), s.netloc.lower(), s.path.rstrip("/"), "", ""))


class Store:
    def __init__(self, path="data/jobs.db"):
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS posts(
            h TEXT PRIMARY KEY, url TEXT, title TEXT, status TEXT, blogger_id TEXT, created_at REAL)""")
        self.db.commit()

    @staticmethod
    def key(url: str) -> str:
        return hashlib.sha256(normalize(url).encode()).hexdigest()

    def seen(self, url: str) -> bool:
        return self.db.execute("SELECT 1 FROM posts WHERE h=?", (self.key(url),)).fetchone() is not None

    def add(self, url, title, status, blogger_id=""):
        self.db.execute("INSERT OR IGNORE INTO posts VALUES (?,?,?,?,?,?)",
                        (self.key(url), url, title, status, blogger_id, time.time()))
        self.db.commit()

    def history(self, limit=50):
        return self.db.execute("SELECT created_at,status,title,url FROM posts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()

    def delete(self, url: str) -> int:
        cursor = self.db.execute("DELETE FROM posts WHERE h=?", (self.key(url),))
        self.db.commit()
        return cursor.rowcount

    def clear_history(self) -> int:
        cursor = self.db.execute("DELETE FROM posts")
        self.db.commit()
        return cursor.rowcount
