from __future__ import annotations


def match_profile(
    full_name: str,
    about: str,
    keywords: list[str],
    stop_words: list[str],
    filter_mode: bool,  # True = фильтр по ключевым словам, False = все подряд
) -> bool:
    """
    Возвращает True если профиль нужно сохранить.

    filter_mode=False — берём всех у кого есть username (без фильтра).
    filter_mode=True  — берём только если есть совпадение по ключевым словам
                        в имени или описании профиля.
    """
    if not filter_mode:
        return True

    text = f"{full_name} {about}".lower()

    # Стоп-слова исключают профиль
    for word in stop_words:
        if word.lower() in text:
            return False

    # Хотя бы одно ключевое слово должно совпасть
    for word in keywords:
        if word.lower() in text:
            return True

    return False
