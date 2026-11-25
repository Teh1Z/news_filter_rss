"""Индекс статей в памяти: токены для поиска по словам и матрица эмбеддингов."""
import threading
from dataclasses import dataclass

import numpy as np

from . import db
from .text import Token, tokenize


@dataclass
class Entry:
    id: int
    feed_id: int
    title: str
    summary: str
    link: str | None
    published: float
    fetched_at: float
    title_tokens: list[Token]
    summary_tokens: list[Token]
    embedding: np.ndarray | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "feed_id": self.feed_id, "title": self.title,
                "summary": self.summary, "link": self.link,
                "published": self.published, "fetched_at": self.fetched_at}


class ArticleIndex:
    def __init__(self):
        self._entries: dict[int, Entry] = {}
        self._lock = threading.Lock()

    def load(self) -> None:
        rows = db.query("SELECT * FROM articles")
        with self._lock:
            self._entries = {r["id"]: self._from_row(r) for r in rows}

    @staticmethod
    def _from_row(r) -> Entry:
        emb = np.frombuffer(r["embedding"], dtype=np.float32) if r["embedding"] else None
        return Entry(
            id=r["id"], feed_id=r["feed_id"], title=r["title"], summary=r["summary"],
            link=r["link"], published=r["published"], fetched_at=r["fetched_at"],
            title_tokens=tokenize(r["title"]), summary_tokens=tokenize(r["summary"]),
            embedding=emb,
        )

    def add(self, article_id: int) -> None:
        row = db.one("SELECT * FROM articles WHERE id = ?", (article_id,))
        if row:
            with self._lock:
                self._entries[article_id] = self._from_row(row)

    def remove_feed(self, feed_id: int) -> None:
        with self._lock:
            self._entries = {k: e for k, e in self._entries.items() if e.feed_id != feed_id}

    def for_feeds(self, feed_ids: set[int]) -> list[Entry]:
        with self._lock:
            items = [e for e in self._entries.values() if e.feed_id in feed_ids]
        items.sort(key=lambda e: (e.published, e.id), reverse=True)
        return items

    def without_embeddings(self) -> list[Entry]:
        with self._lock:
            return [e for e in self._entries.values() if e.embedding is None]

    def __len__(self) -> int:
        return len(self._entries)


index = ArticleIndex()
