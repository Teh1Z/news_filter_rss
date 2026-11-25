"""SQLite-хранилище: пользователи, каналы, подписки, фильтры, статьи, посты тестового канала."""
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS feeds (
    id           INTEGER PRIMARY KEY,
    url          TEXT NOT NULL UNIQUE,
    title        TEXT,
    is_local     INTEGER NOT NULL DEFAULT 0,
    last_fetched REAL,
    last_error   TEXT,
    created_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, feed_id)
);
CREATE TABLE IF NOT EXISTS filters (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('keyword', 'semantic')),
    name       TEXT NOT NULL,
    query      TEXT NOT NULL,
    threshold  REAL,
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS articles (
    id         INTEGER PRIMARY KEY,
    feed_id    INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    guid       TEXT NOT NULL,
    title      TEXT NOT NULL,
    summary    TEXT NOT NULL,
    link       TEXT,
    published  REAL NOT NULL,
    fetched_at REAL NOT NULL,
    embedding  BLOB,
    UNIQUE (feed_id, guid)
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published DESC);
CREATE TABLE IF NOT EXISTS local_posts (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    author     TEXT,
    created_at REAL NOT NULL
);
"""

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()


def init() -> None:
    global _conn
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.executescript(SCHEMA)
    _conn.commit()


@contextmanager
def tx():
    """Транзакция под общим замком (sqlite-соединение одно на процесс)."""
    with _lock:
        try:
            yield _conn
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise


def query(sql: str, params=()) -> list[sqlite3.Row]:
    with _lock:
        return _conn.execute(sql, params).fetchall()


def one(sql: str, params=()) -> sqlite3.Row | None:
    with _lock:
        return _conn.execute(sql, params).fetchone()


def now() -> float:
    return time.time()


# --- каналы ---------------------------------------------------------------

def ensure_feed(url: str, is_local: bool = False, title: str | None = None) -> tuple[int, bool]:
    """Возвращает (feed_id, создан_ли_новый)."""
    with tx() as c:
        row = c.execute("SELECT id FROM feeds WHERE url = ?", (url,)).fetchone()
        if row:
            return row["id"], False
        cur = c.execute(
            "INSERT INTO feeds (url, title, is_local, created_at) VALUES (?, ?, ?, ?)",
            (url, title, int(is_local), now()),
        )
        return cur.lastrowid, True


def subscribe(user_id: int, feed_id: int) -> None:
    with tx() as c:
        c.execute("INSERT OR IGNORE INTO subscriptions VALUES (?, ?)", (user_id, feed_id))


def subscribe_all_users(feed_id: int) -> None:
    with tx() as c:
        c.execute("INSERT OR IGNORE INTO subscriptions SELECT id, ? FROM users", (feed_id,))
