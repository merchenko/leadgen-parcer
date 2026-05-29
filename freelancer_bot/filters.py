from __future__ import annotations

import re
from dataclasses import dataclass


MIN_SCORE = 3

# Редактируйте этот словарь под себя. Чем выше вес, тем больше шанс, что лид уйдет в бот.
KEYWORDS: dict[str, int] = {
    "telegram bot": 5,
    "telegram-бот": 5,
    "телеграм бот": 5,
    "телеграм-бот": 5,
    "тг бот": 5,
    "tg bot": 5,
    "бот в тг": 5,
    "бот для": 4,
    "чат-бот": 4,
    "bot api": 4,
    "mini app": 4,
    "mini apps": 4,
    "web app telegram": 4,
    "webapp": 3,
    "парсер": 4,
    "парсинг": 4,
    "скрипт": 3,
    "автоматизация": 3,
    "интеграция": 2,
    "api": 2,
    "python": 2,
    "django": 2,
    "fastapi": 2,
    "backend": 2,
    "бекенд": 2,
    "разработчик": 1,
    "доработка": 2,
    "нейросеть": 2,
    "gpt": 2,
    "ai агент": 3,
    "ии агент": 3,
    "openai": 2,
    "оплата": 1,
    "проектная": 1,
    "разовая": 1,
}

# Стоп-слова специально жесткие: лучше пропустить шумный пост, чем засыпать себя мусором.
STOP_WORDS: list[str] = [
    "smm",
    "смм",
    "таргетолог",
    "директолог",
    "маркетолог",
    "копирайтер",
    "рерайтер",
    "дизайнер логотип",
    "иллюстратор",
    "монтажер",
    "рилсмейкер",
    "сторисмейкер",
    "ассистент",
    "менеджер по продажам",
    "оператор",
    "колл-центр",
    "набор текста",
    "расшифровка аудио",
    "отзывы",
    "оставлять отзывы",
    "лайки",
    "подписки",
    "без опыта",
    "ежедневные выплаты",
    "инвестиции",
    "ставки",
    "букмекер",
    "казино",
    "gambling",
    "onlyfans",
    "18+",
    "офис",
    "только офис",
    "полный день в офисе",
]


@dataclass(frozen=True)
class MatchResult:
    accepted: bool
    score: int
    matched_keywords: tuple[str, ...]
    rejected_by: tuple[str, ...]


def normalize(text: str) -> str:
    lowered = text.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", lowered).strip()


def find_terms(text: str, terms: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized = normalize(text)
    matches: list[str] = []
    for term in terms:
        term_normalized = normalize(term)
        if term_normalized and term_normalized in normalized:
            matches.append(term)
    return tuple(matches)


def match_text(text: str) -> MatchResult:
    return match_text_dynamic(text, KEYWORDS, STOP_WORDS)


def match_text_dynamic(text: str, keywords: dict[str, int], stop_words: list[str]) -> MatchResult:
    rejected_by = find_terms(text, tuple(stop_words))
    if rejected_by:
        return MatchResult(False, 0, (), rejected_by)

    normalized = normalize(text)
    matched: list[str] = []
    score = 0
    for keyword, weight in keywords.items():
        if normalize(keyword) in normalized:
            matched.append(keyword)
            score += weight

    accepted = score >= MIN_SCORE
    return MatchResult(accepted, score, tuple(matched), ())
