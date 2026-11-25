"""Нормализация текста: очистка HTML, токенизация, стемминг (русский + английский)."""
import html
import re
from dataclasses import dataclass
from functools import lru_cache

import snowballstemmer

_ru = snowballstemmer.stemmer("russian")
_en = snowballstemmer.stemmer("english")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CYR_RE = re.compile(r"[а-я]")


def clean_html(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw or "")
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def normalize(word: str) -> str:
    return word.lower().replace("ё", "е")


@lru_cache(maxsize=200_000)
def stem(word: str) -> str:
    """Стем нормализованного слова; язык определяется по алфавиту.

    Короткие слова (≤3 букв: «ИИ», «ЦБ», «AI») не стеммятся — иначе «ии» → «и».
    """
    if len(word) <= 3:
        return word
    if _CYR_RE.search(word):
        return _ru.stemWord(word)
    if word.isascii() and word.isalpha():
        return _en.stemWord(word)
    return word


@dataclass(frozen=True)
class Token:
    surface: str  # слово как в тексте
    norm: str     # нижний регистр, ё→е
    stem: str


def tokenize(text: str) -> list[Token]:
    out = []
    for m in _TOKEN_RE.finditer(text):
        surface = m.group(0)
        norm = normalize(surface)
        out.append(Token(surface, norm, stem(norm)))
    return out
