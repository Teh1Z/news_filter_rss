"""Применение фильтров пользователя к статьям его каналов.

Новость попадает в ленту, если её пропустил хотя бы один включённый фильтр (OR).
Для каждой новости возвращается список сработавших фильтров с техническими
деталями: какие термы и в каких словоформах нашлись (ключевые слова) или
косинус/z-оценка/порог/лучшая формулировка темы (семантика).
"""
import numpy as np

from . import config, db, keyword_engine
from .index import Entry, index
from .semantic_engine import engine


def user_filters(user_id: int, only_enabled: bool = True) -> list[dict]:
    sql = "SELECT * FROM filters WHERE user_id = ?"
    if only_enabled:
        sql += " AND enabled = 1"
    return [dict(r) for r in db.query(sql + " ORDER BY id", (user_id,))]


def user_feed_ids(user_id: int) -> set[int]:
    return {r["feed_id"] for r in db.query(
        "SELECT feed_id FROM subscriptions WHERE user_id = ?", (user_id,))}


def _keyword_results(flt: dict, entries: list[Entry]) -> list[dict]:
    terms = keyword_engine.parse_query(flt["query"])
    out = []
    for e in entries:
        r = keyword_engine.match(terms, {"title": e.title_tokens, "summary": e.summary_tokens})
        out.append({"matched": r["matched"], "details": r})
    return out


def _semantic_results(flt: dict, entries: list[Entry]) -> list[dict]:
    threshold = flt["threshold"] if flt["threshold"] is not None else config.DEFAULT_SEMANTIC_THRESHOLD
    out = [{"matched": False, "details": {"error": "нет эмбеддинга (модель ещё загружается)"}}
           for _ in entries]
    if not engine.ready:
        return out
    idx = [i for i, e in enumerate(entries) if e.embedding is not None]
    if not idx:
        return out
    emb = np.stack([entries[i].embedding for i in idx])
    mu, sd = engine.background(emb)
    z, cos, best, phrases = engine.score(flt["query"], emb, mu, sd)
    for k, i in enumerate(idx):
        out[i] = {"matched": bool(z[k] >= threshold), "details": {
            "z": round(float(z[k]), 2), "cosine": round(float(cos[k]), 3),
            "bg_mean": round(float(mu[k]), 3), "bg_std": round(float(sd[k]), 3),
            "threshold": threshold, "best_phrase": phrases[int(best[k])],
            "model": engine.model_name,
        }}
    return out


def build_feed(user_id: int, show_all: bool = False, limit: int = config.FEED_LIMIT) -> dict:
    filters = user_filters(user_id)
    entries = index.for_feeds(user_feed_ids(user_id))
    per_filter = []
    for flt in filters:
        fn = _keyword_results if flt["kind"] == "keyword" else _semantic_results
        per_filter.append((flt, fn(flt, entries)))

    items, total_matched = [], 0
    for i, e in enumerate(entries):
        matches, misses = [], []
        for flt, results in per_filter:
            res = results[i]
            info = {"filter_id": flt["id"], "name": flt["name"], "kind": flt["kind"],
                    "query": flt["query"], **res}
            (matches if res["matched"] else misses).append(info)
        if matches:
            total_matched += 1
        if (matches or show_all) and len(items) < limit:
            item = e.to_dict()
            item["matches"] = matches
            if show_all:
                item["misses"] = misses
            items.append(item)
    return {"items": items, "total_candidates": len(entries), "total_matched": total_matched,
            "filters": len(filters), "semantic_ready": engine.ready}
