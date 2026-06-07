from __future__ import annotations

from .storage import ProfileRecord


def format_profile(p: ProfileRecord, keywords: list[str]) -> str:
    """Форматирует карточку профиля для отправки в Telegram."""

    lines: list[str] = []

    # Имя + ссылка
    lines.append(f"👤 <b>{p.full_name}</b>")

    if p.username:
        lines.append(f"🔗 @{p.username}")
    else:
        lines.append(f"🔗 <a href='{p.profile_url}'>Открыть профиль</a>")

    # Описание профиля
    if p.about:
        # Подсвечиваем совпавшие ключевые слова
        about_display = p.about
        for kw in keywords:
            idx = about_display.lower().find(kw.lower())
            if idx != -1:
                original = about_display[idx:idx + len(kw)]
                about_display = about_display.replace(original, f"<b>{original}</b>", 1)
        lines.append(f"\n📝 {about_display}")

    # Источник
    lines.append(f"\n📌 Источник: {p.source_chat}")

    return "\n".join(lines)
