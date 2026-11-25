"""Семантический движок: эмбеддинги BAAI/bge-m3 + калибровка по фоновым рубрикам.

Сырой косинус между короткой темой («спорт») и новостью плохо сравним между
статьями: «общие» тексты (погода, курьёзы) похожи сразу на всё. Поэтому для каждой
статьи считаем похожесть на набор из 40 нейтральных рубрик (BACKGROUND_TOPICS),
её среднее μ и стандартное отклонение σ. Оценка темы для статьи — z-оценка:

    z = (cos(статья, тема) − μ_статьи) / σ_статьи

т.е. «насколько тема ближе к статье, чем типичная рубрика». Статья проходит
фильтр, если z ≥ порога (по умолчанию 2.0). Тема может состоять из нескольких
формулировок через запятую; берётся лучшая (максимальный cos).
"""
import logging
import threading

import numpy as np

from . import config

log = logging.getLogger(__name__)

BACKGROUND_TOPICS = [
    "политика", "экономика", "спорт", "наука", "технологии", "культура",
    "происшествия", "погода", "здоровье", "общество", "путешествия",
    "автомобили", "кино и музыка", "образование", "военные действия",
    "недвижимость", "криминал", "мода", "еда", "религия",
    "внешняя политика и дипломатия", "выборы", "бизнес и компании", "финансовые рынки",
    "промышленность и энергетика", "программирование", "интернет и соцсети", "гаджеты",
    "медицина", "экология и природа", "животные", "знаменитости", "транспорт и авиация",
    "армия и оружие", "суд и право", "катастрофы", "история", "наука и исследования",
    "игры", "семья и дети",
]


class SemanticEngine:
    def __init__(self, model_name: str = config.EMBED_MODEL):
        self.model_name = model_name
        self.model = None
        self.error: str | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._bg: np.ndarray | None = None
        self._topic_cache: dict[str, np.ndarray] = {}

    # --- загрузка ---------------------------------------------------------
    def load(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer

            log.info("Загрузка модели %s ...", self.model_name)
            try:  # сначала из локального кэша: демо работает без интернета
                model = SentenceTransformer(self.model_name, local_files_only=True)
            except Exception:
                model = SentenceTransformer(self.model_name)
            model.max_seq_length = config.EMBED_MAX_SEQ
            self.model = model
            self._bg = self._encode(BACKGROUND_TOPICS)
            log.info("Модель загружена (%s)", model.device)
        except Exception as e:  # приложение продолжает работать без семантики
            self.error = f"{type(e).__name__}: {e}"
            log.exception("Не удалось загрузить модель")
        finally:
            self._ready.set()

    @property
    def ready(self) -> bool:
        return self.model is not None

    def wait(self, timeout: float | None = None) -> bool:
        self._ready.wait(timeout)
        return self.ready

    def _encode(self, texts: list[str]) -> np.ndarray:
        with self._lock:  # модель не потокобезопасна
            emb = self.model.encode(texts, batch_size=16, normalize_embeddings=True,
                                    convert_to_numpy=True, show_progress_bar=False)
        return emb.astype(np.float32)

    # --- статьи -----------------------------------------------------------
    def embed_articles(self, texts: list[str]) -> np.ndarray:
        return self._encode(texts)

    def background(self, emb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """μ и σ похожести каждой статьи на фоновые рубрики."""
        sims = emb @ self._bg.T
        return sims.mean(axis=1), np.maximum(sims.std(axis=1), 1e-6)

    # --- темы -------------------------------------------------------------
    @staticmethod
    def topic_phrases(query: str) -> list[str]:
        return [p.strip() for p in query.replace("\n", ",").split(",") if p.strip()]

    def topic_vectors(self, query: str) -> tuple[list[str], np.ndarray]:
        phrases = self.topic_phrases(query)
        missing = [p for p in phrases if p not in self._topic_cache]
        if missing:
            for p, v in zip(missing, self._encode(missing)):
                self._topic_cache[p] = v
        return phrases, np.stack([self._topic_cache[p] for p in phrases])

    def score(self, query: str, emb: np.ndarray, bg_mean: np.ndarray, bg_std: np.ndarray):
        """Векторная оценка темы по матрице статей.

        Возвращает (z, cos, индекс лучшей формулировки, список формулировок).
        Берётся максимум по формулировкам (центроид формулировок на тестах давал
        больше ложных срабатываний).
        """
        phrases, tv = self.topic_vectors(query)
        sims = emb @ tv.T                      # (статьи, формулировки)
        best = sims.argmax(axis=1)
        cos = sims[np.arange(len(sims)), best]
        z = (cos - bg_mean) / bg_std
        return z, cos, best, phrases


engine = SemanticEngine()
