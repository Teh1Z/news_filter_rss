"""Настройки приложения (переопределяются переменными окружения)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("NEWS_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "news.db"
FEEDS_FILE = Path(os.getenv("NEWS_FEEDS_FILE", BASE_DIR / "feeds.txt"))

HOST = os.getenv("NEWS_HOST", "127.0.0.1")
PORT = int(os.getenv("NEWS_PORT", "8000"))
# Адрес, по которому сервис сам забирает свой тестовый RSS-канал
SELF_URL = os.getenv("NEWS_SELF_URL", f"http://127.0.0.1:{PORT}")
TEST_FEED_URL = f"{SELF_URL}/rss/test.xml"

POLL_INTERVAL = int(os.getenv("NEWS_POLL_INTERVAL", "120"))  # секунды
FETCH_TIMEOUT = float(os.getenv("NEWS_FETCH_TIMEOUT", "20"))
MAX_ITEMS_PER_FEED = int(os.getenv("NEWS_MAX_ITEMS_PER_FEED", "100"))

EMBED_MODEL = os.getenv("NEWS_EMBED_MODEL", "BAAI/bge-m3")
EMBED_MAX_SEQ = int(os.getenv("NEWS_EMBED_MAX_SEQ", "256"))
DEFAULT_SEMANTIC_THRESHOLD = float(os.getenv("NEWS_SEMANTIC_THRESHOLD", "2.0"))

FEED_LIMIT = 300  # сколько новостей максимум отдавать в ленту
