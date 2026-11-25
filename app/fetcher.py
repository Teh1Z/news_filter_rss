"""Периодический сбор RSS/Atom-каналов, дедупликация, расчёт эмбеддингов."""
import asyncio
import calendar
import logging
import time

import feedparser
import httpx

from . import config, db, events
from .index import index
from .semantic_engine import engine
from .text import clean_html

log = logging.getLogger(__name__)

_wakeup = asyncio.Event()
_feed_locks: dict[int, asyncio.Lock] = {}
HEADERS = {"User-Agent": "Mozilla/5.0 (NewsFilter student project; +rss)"}


def _entry_time(entry) -> float:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(key)
        if t:
            return min(float(calendar.timegm(t)), time.time())
    return time.time()


def _entry_summary(entry) -> str:
    raw = entry.get("summary") or entry.get("description") or ""
    if not raw and entry.get("content"):
        raw = entry["content"][0].get("value", "")
    return clean_html(raw)[:2000]


def _embedding_text(title: str, summary: str) -> str:
    return f"{title}. {summary}"[:1500]


def _store_embeddings(rows: list[tuple[int, str]]) -> None:
    """rows: [(article_id, text)] — считает и сохраняет эмбеддинги (блокирующе)."""
    if not rows or not engine.ready:
        return
    emb = engine.embed_articles([t for _, t in rows])
    with db.tx() as c:
        for (aid, _), vec in zip(rows, emb):
            c.execute("UPDATE articles SET embedding = ? WHERE id = ?", (vec.tobytes(), aid))


async def fetch_feed(feed_id: int, client: httpx.AsyncClient | None = None) -> int:
    lock = _feed_locks.setdefault(feed_id, asyncio.Lock())
    async with lock:
        feed = db.one("SELECT * FROM feeds WHERE id = ?", (feed_id,))
        if not feed:
            return 0
        own_client = client is None
        client = client or httpx.AsyncClient(timeout=config.FETCH_TIMEOUT, headers=HEADERS,
                                             follow_redirects=True)
        try:
            resp = await client.get(feed["url"])
            resp.raise_for_status()
            parsed = await asyncio.to_thread(feedparser.parse, resp.content)
            if parsed.bozo and not parsed.entries:
                raise ValueError(f"не похоже на RSS/Atom: {parsed.bozo_exception}")
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"[:300]
            log.warning("Канал %s: %s", feed["url"], msg)
            with db.tx() as c:
                c.execute("UPDATE feeds SET last_fetched = ?, last_error = ? WHERE id = ?",
                          (time.time(), msg, feed_id))
            events.publish({"type": "feeds"})
            return 0
        finally:
            if own_client:
                await client.aclose()

        title = clean_html(parsed.feed.get("title", "")) or feed["title"] or feed["url"]
        known = {r["guid"] for r in db.query("SELECT guid FROM articles WHERE feed_id = ?", (feed_id,))}
        new_rows = []
        now = time.time()
        with db.tx() as c:
            for entry in parsed.entries[: config.MAX_ITEMS_PER_FEED]:
                guid = entry.get("id") or entry.get("link") or entry.get("title")
                if not guid or guid in known:
                    continue
                known.add(guid)
                t = clean_html(entry.get("title", "")) or "(без заголовка)"
                s = _entry_summary(entry)
                cur = c.execute(
                    "INSERT OR IGNORE INTO articles (feed_id, guid, title, summary, link, published, fetched_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (feed_id, guid, t, s, entry.get("link"), _entry_time(entry), now))
                if cur.rowcount:
                    new_rows.append((cur.lastrowid, _embedding_text(t, s)))
            c.execute("UPDATE feeds SET title = ?, last_fetched = ?, last_error = NULL WHERE id = ?",
                      (title, now, feed_id))

        if new_rows:
            await asyncio.to_thread(_store_embeddings, new_rows)
            for aid, _ in new_rows:
                index.add(aid)
            log.info("Канал %s: +%d новостей", title, len(new_rows))
            events.publish({"type": "articles", "feed_id": feed_id, "count": len(new_rows)})
        events.publish({"type": "feeds"})
        return len(new_rows)


async def fetch_all() -> int:
    feeds = db.query("SELECT id FROM feeds")
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=config.FETCH_TIMEOUT, headers=HEADERS,
                                 follow_redirects=True) as client:
        async def one(fid):
            async with sem:
                return await fetch_feed(fid, client)
        counts = await asyncio.gather(*(one(r["id"]) for r in feeds))
    return sum(counts)


async def backfill_embeddings() -> None:
    """Досчитать эмбеддинги статей, собранных до загрузки модели."""
    await asyncio.to_thread(engine.wait)
    while engine.ready:
        missing = index.without_embeddings()[:256]
        if not missing:
            break
        await asyncio.to_thread(_store_embeddings,
                                [(e.id, _embedding_text(e.title, e.summary)) for e in missing])
        for e in missing:
            index.add(e.id)
        events.publish({"type": "articles", "count": len(missing)})


def wake_up() -> None:
    _wakeup.set()


async def poll_loop() -> None:
    while True:
        try:
            await fetch_all()
            await backfill_embeddings()
        except Exception:
            log.exception("Ошибка цикла сбора")
        _wakeup.clear()
        try:
            await asyncio.wait_for(_wakeup.wait(), timeout=config.POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass
