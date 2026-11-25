"""Движок поиска по ключевым словам.

Синтаксис запроса — термы через запятую или с новой строки:
    нейросеть, ИИ, машинное обучение   — любой из термов (OR), сравнение по стемам,
                                          поэтому «нейросети», «нейросетью» тоже совпадут
    "машинное обучение"                 — фраза: слова подряд (кавычки необязательны)
    нейро*                              — префикс: любое слово, начинающееся на «нейро»
    =Apple                              — точная словоформа, без стемминга
                                          (аббревиатуры в верхнем регистре — всегда точно)
    +Россия                             — обязательный терм (должен встретиться)
    -реклама                            — исключающий терм (новость отбрасывается)
"""
from dataclasses import dataclass, field

from .text import Token, normalize, stem, tokenize


@dataclass
class Word:
    value: str
    mode: str  # 'stem' | 'exact' | 'prefix'

    def matches(self, tok: Token) -> bool:
        if self.mode == "stem":
            return tok.stem == self.value
        if self.mode == "exact":
            return tok.norm == self.value
        return tok.norm.startswith(self.value)


@dataclass
class Term:
    raw: str
    role: str  # 'any' | 'required' | 'exclude'
    words: list[Word] = field(default_factory=list)

    @property
    def method(self) -> str:
        kinds = {w.mode for w in self.words}
        base = "фраза" if len(self.words) > 1 else "слово"
        how = {"stem": "по основе (стемминг)", "exact": "точная форма", "prefix": "по префиксу"}
        return f"{base}, " + ", ".join(how[k] for k in sorted(kinds))


def parse_query(query: str) -> list[Term]:
    terms = []
    for chunk in query.replace("\n", ",").split(","):
        raw = chunk.strip()
        if not raw:
            continue
        role = "any"
        if raw[0] in "+-":
            role = "required" if raw[0] == "+" else "exclude"
            raw = raw[1:].strip()
        exact = raw.startswith("=")
        if exact:
            raw = raw[1:].strip()
        raw = raw.strip('"«»“”\'')
        words = []
        parts = raw.split()
        for i, part in enumerate(parts):
            is_prefix = part.endswith("*")
            toks = tokenize(part.rstrip("*"))
            for j, tok in enumerate(toks):
                if is_prefix and j == len(toks) - 1:
                    words.append(Word(tok.norm, "prefix"))
                elif exact or (len(tok.surface) > 1 and tok.surface.isupper()):
                    # аббревиатуры (NASA, ЦБ, ВТБ) ищем как точную форму
                    words.append(Word(tok.norm, "exact"))
                else:
                    words.append(Word(tok.stem, "stem"))
        if words:
            terms.append(Term(raw=chunk.strip(), role=role, words=words))
    return terms


def _find(term: Term, tokens: list[Token]) -> list[str]:
    """Все вхождения терма (как подряд идущие токены); возвращает найденные формы."""
    n = len(term.words)
    found = []
    for i in range(len(tokens) - n + 1):
        if all(term.words[k].matches(tokens[i + k]) for k in range(n)):
            found.append(" ".join(t.surface for t in tokens[i:i + n]))
    return found


def match(terms: list[Term], fields: dict[str, list[Token]]) -> dict:
    """Проверяет статью. fields: {'title': [...], 'summary': [...]}.

    Возвращает {'matched': bool, 'hits': [...], 'excluded': [...], 'missing': [...]},
    где hits — совпавшие термы с найденными формами и полем статьи.
    """
    hits, excluded, missing = [], [], []
    any_terms = [t for t in terms if t.role == "any"]
    any_hit = False
    for term in terms:
        occurrences = []
        for field_name, tokens in fields.items():
            forms = _find(term, tokens)
            if forms:
                occurrences.append({"field": field_name, "forms": sorted(set(forms)), "count": len(forms)})
        info = {"term": term.raw, "method": term.method, "where": occurrences}
        if term.role == "exclude":
            if occurrences:
                excluded.append(info)
        elif occurrences:
            hits.append(info)
            any_hit = any_hit or term.role == "any"
        elif term.role == "required":
            missing.append(term.raw)
    has_positive = any(t.role != "exclude" for t in terms)
    matched = (
        has_positive
        and not excluded
        and not missing
        and (any_hit or not any_terms)
    )
    return {"matched": matched, "hits": hits, "excluded": excluded, "missing": missing}
