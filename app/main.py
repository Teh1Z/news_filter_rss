"""FastAPI-приложение: REST API, тестовый RSS-канал, SSE-поток событий, статика."""
import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager
from email.utils import formatdate
from xml.sax.saxutils import escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, events, fetcher, filtering
from .index import index
from .semantic_engine import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

STATIC_DIR = config.BASE_DIR / "app" / "static"
DEFAULT_FEED_IDS: set[int] = set()
_bg_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


def _test_feed_id() -> int:
    return db.one("SELECT id FROM feeds WHERE is_local = 1")["id"]


def _read_feeds_file() -> list[str]:
    if not config.FEEDS_FILE.exists():
        return []
    lines = config.FEEDS_FILE.read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def _bootstrap() -> None:
    db.init()
    # тестовый канал: при смене порта обновляем URL
    row = db.one("SELECT id FROM feeds WHERE is_local = 1")
    if row:
        with db.tx() as c:
            c.execute("UPDATE feeds SET url = ? WHERE id = ?", (config.TEST_FEED_URL, row["id"]))
        DEFAULT_FEED_IDS.add(row["id"])
    else:
        fid, _ = db.ensure_feed(config.TEST_FEED_URL, is_local=True, title="Тестовый канал")
        DEFAULT_FEED_IDS.add(fid)
    for url in _read_feeds_file():
        fid, created = db.ensure_feed(url)
        DEFAULT_FEED_IDS.add(fid)
        if created:  # новый канал из feeds.txt - подписываем всех
            db.subscribe_all_users(fid)
            log.info("Из feeds.txt добавлен канал %s", url)
    if not db.one("SELECT id FROM users"):
        uid = _create_user("demo")
        with db.tx() as c:
            c.execute("INSERT INTO filters (user_id, kind, name, query, threshold, created_at) VALUES (?,?,?,?,?,?)",
                      (uid, "keyword", "ИИ", "ИИ, AI, нейросеть, искусственный интеллект, машинное обучение, LLM", None, time.time()))
            c.execute("INSERT INTO filters (user_id, kind, name, query, threshold, created_at) VALUES (?,?,?,?,?,?)",
                      (uid, "semantic", "Космос", "космос, астрономия", None, time.time()))
    index.load()


def _create_user(name: str) -> int:
    with db.tx() as c:
        uid = c.execute("INSERT INTO users (name, created_at) VALUES (?, ?)", (name, time.time())).lastrowid
        for fid in DEFAULT_FEED_IDS:
            c.execute("INSERT OR IGNORE INTO subscriptions VALUES (?, ?)", (uid, fid))
    return uid


@asynccontextmanager
async def lifespan(app: FastAPI):
    _bootstrap()
    threading.Thread(target=engine.load, daemon=True).start()
    _spawn(fetcher.poll_loop())
    yield
    for t in list(_bg_tasks):
        t.cancel()


app = FastAPI(title="News Filter", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index_page():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def status():
    return {"articles": len(index), "semantic_ready": engine.ready, "semantic_error": engine.error,
            "model": engine.model_name, "poll_interval": config.POLL_INTERVAL,
            "default_threshold": config.DEFAULT_SEMANTIC_THRESHOLD}


@app.post("/api/refresh")
def refresh():
    fetcher.wake_up()
    return {"ok": True}


class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)


def _user_or_404(user_id: int):
    u = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    return u


@app.get("/api/users")
def list_users():
    return [dict(r) for r in db.query("SELECT id, name FROM users ORDER BY id")]


@app.post("/api/users")
def create_user(body: UserIn):
    name = body.name.strip()
    if db.one("SELECT id FROM users WHERE name = ?", (name,)):
        raise HTTPException(400, "Пользователь с таким именем уже есть")
    return {"id": _create_user(name), "name": name}


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int):
    _user_or_404(user_id)
    with db.tx() as c:
        c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"ok": True}


class FeedIn(BaseModel):
    url: str = Field(min_length=8, max_length=1000)


class SubscriptionIn(BaseModel):
    subscribed: bool


@app.get("/api/users/{user_id}/feeds")
def list_feeds(user_id: int):
    _user_or_404(user_id)
    rows = db.query("""
        SELECT f.*, (s.user_id IS NOT NULL) AS subscribed,
               (SELECT COUNT(*) FROM articles a WHERE a.feed_id = f.id) AS articles
        FROM feeds f LEFT JOIN subscriptions s ON s.feed_id = f.id AND s.user_id = ?
        ORDER BY f.is_local DESC, f.id""", (user_id,))
    return [dict(r) for r in rows]


@app.post("/api/users/{user_id}/feeds")
async def add_feed(user_id: int, body: FeedIn):
    _user_or_404(user_id)
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL должен начинаться с http:// или https://")
    fid, _ = db.ensure_feed(url)
    db.subscribe(user_id, fid)
    new = await fetcher.fetch_feed(fid)
    feed = db.one("SELECT * FROM feeds WHERE id = ?", (fid,))
    return {"feed": dict(feed), "new_articles": new}


@app.put("/api/users/{user_id}/feeds/{feed_id}")
def set_subscription(user_id: int, feed_id: int, body: SubscriptionIn):
    _user_or_404(user_id)
    with db.tx() as c:
        if body.subscribed:
            c.execute("INSERT OR IGNORE INTO subscriptions VALUES (?, ?)", (user_id, feed_id))
        else:
            c.execute("DELETE FROM subscriptions WHERE user_id = ? AND feed_id = ?", (user_id, feed_id))
    return {"ok": True}


@app.delete("/api/feeds/{feed_id}")
def delete_feed(feed_id: int):
    feed = db.one("SELECT * FROM feeds WHERE id = ?", (feed_id,))
    if not feed:
        raise HTTPException(404, "Канал не найден")
    if feed["is_local"]:
        raise HTTPException(400, "Тестовый канал удалить нельзя")
    with db.tx() as c:
        c.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
    index.remove_feed(feed_id)
    return {"ok": True}


class FilterIn(BaseModel):
    kind: str = Field(pattern="^(keyword|semantic)$")
    name: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=2000)
    threshold: float | None = None


class FilterPatch(BaseModel):
    name: str | None = None
    query: str | None = None
    threshold: float | None = None
    enabled: bool | None = None


@app.get("/api/users/{user_id}/filters")
def list_filters(user_id: int):
    _user_or_404(user_id)
    return filtering.user_filters(user_id, only_enabled=False)


@app.post("/api/users/{user_id}/filters")
def create_filter(user_id: int, body: FilterIn):
    _user_or_404(user_id)
    threshold = body.threshold if body.kind == "semantic" else None
    with db.tx() as c:
        fid = c.execute(
            "INSERT INTO filters (user_id, kind, name, query, threshold, created_at) VALUES (?,?,?,?,?,?)",
            (user_id, body.kind, body.name.strip(), body.query.strip(), threshold, time.time())).lastrowid
    return dict(db.one("SELECT * FROM filters WHERE id = ?", (fid,)))


@app.patch("/api/filters/{filter_id}")
def update_filter(filter_id: int, body: FilterPatch):
    if not db.one("SELECT id FROM filters WHERE id = ?", (filter_id,)):
        raise HTTPException(404, "Фильтр не найден")
    fields = body.model_dump(exclude_unset=True)
    if fields:
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = [int(v) if isinstance(v, bool) else v for v in fields.values()]
        with db.tx() as c:
            c.execute(f"UPDATE filters SET {cols} WHERE id = ?", (*vals, filter_id))
    return dict(db.one("SELECT * FROM filters WHERE id = ?", (filter_id,)))


@app.delete("/api/filters/{filter_id}")
def delete_filter(filter_id: int):
    with db.tx() as c:
        c.execute("DELETE FROM filters WHERE id = ?", (filter_id,))
    return {"ok": True}


@app.get("/api/users/{user_id}/feed")
async def user_feed(user_id: int, all: bool = False):
    _user_or_404(user_id)
    # семантика считает эмбеддинги новых тем - уводим из event loop
    return await asyncio.to_thread(filtering.build_feed, user_id, all)


@app.get("/api/events")
async def sse(request: Request):
    q = events.subscribe()

    async def stream():
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


class PostIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=5000)
    author: str | None = Field(default=None, max_length=100)


@app.get("/api/test-posts")
def list_posts():
    return [dict(r) for r in db.query("SELECT * FROM local_posts ORDER BY id DESC LIMIT 50")]


@app.post("/api/test-posts")
async def create_post(body: PostIn):
    with db.tx() as c:
        pid = c.execute("INSERT INTO local_posts (title, body, author, created_at) VALUES (?,?,?,?)",
                        (body.title.strip(), body.body.strip(), body.author, time.time())).lastrowid
    # сразу забираем свой канал по HTTP, как любой внешний RSS
    _spawn(fetcher.fetch_feed(_test_feed_id()))
    return {"id": pid}


@app.get("/rss/test.xml")
def test_rss():
    posts = db.query("SELECT * FROM local_posts ORDER BY id DESC LIMIT 100")
    items = []
    for p in posts:
        link = f"{config.SELF_URL}/rss/test/{p['id']}"
        author = f"<author>{escape(p['author'])}</author>" if p["author"] else ""
        items.append(
            f"<item><title>{escape(p['title'])}</title><link>{link}</link>"
            f"<guid isPermaLink=\"false\">test-post-{p['id']}</guid>"
            f"<description>{escape(p['body'])}</description>{author}"
            f"<pubDate>{formatdate(p['created_at'], usegmt=True)}</pubDate></item>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
           "<title>Тестовый канал</title>"
           f"<link>{config.SELF_URL}/</link>"
           "<description>Локальный RSS-канал для демонстрации</description>"
           + "".join(items) + "</channel></rss>")
    return Response(xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/rss/test/{post_id}", include_in_schema=False)
def test_post_page(post_id: int):
    p = db.one("SELECT * FROM local_posts WHERE id = ?", (post_id,))
    if not p:
        raise HTTPException(404)
    html = f"<meta charset=utf-8><h1>{escape(p['title'])}</h1><p>{escape(p['body'])}</p>"
    return Response(html, media_type="text/html; charset=utf-8")


def run() -> None:
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    run()
