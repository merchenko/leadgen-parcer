from __future__ import annotations

import csv
import io
import logging

from telethon import TelegramClient, Button, events

from .storage import ProfileStorage

log = logging.getLogger(__name__)

S_STORIES   = "stories_enabled"
S_REACTIONS = "reactions_stories_enabled"
S_REACTOR   = "reactor_enabled"
S_FILTER    = "filter_mode"


def _on(storage: ProfileStorage, key: str) -> bool:
    return storage.get_setting(key, "1") == "1"


def _icon(val: bool) -> str:
    return "✅" if val else "❌"


MAIN_KEYBOARD = [
    [Button.text("📊 Статус"),      Button.text("👥 База лидов")],
    [Button.text("⚙️ Действия"),    Button.text("📱 Аккаунты")],
    [Button.text("📤 Экспорт CSV"), Button.text("📡 Чаты")],
    [Button.text("🎯 Фриланс")],
]


def register_commands(
    bot: TelegramClient,
    storage: ProfileStorage,
    admin_ids: list[int],
    parser_ref: list,
) -> None:

    def is_admin(chat_id: int) -> bool:
        return chat_id in admin_ids

    # ── /start и /menu ────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"^/(start|menu)$"))
    async def cmd_start(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        await event.respond(
            "👋 <b>Выбери раздел:</b>",
            buttons=MAIN_KEYBOARD,
            parse_mode="html",
        )

    # ── 📊 Статус ─────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern="📊 Статус"))
    async def cmd_status(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        s = storage.full_stats()
        cat_lines = "\n".join(f"  • {k}: {v}" for k, v in sorted(s["by_category"].items())) or "  пусто"
        acc_lines = "\n".join(f"  • {k}: {v}" for k, v in s["by_account"].items()) or "  нет данных"
        text = (
            f"📊 <b>Статус</b>\n\n"
            f"👥 <b>База лидов: {s['total_profiles']}</b>\n{cat_lines}\n\n"
            f"👁 Сторис просмотрено: {s['stories_viewed']}\n"
            f"  • Сегодня: {s['stories_today']}\n"
            f"  • Реакций на сторис: {s['stories_reactions']}\n\n"
            f"❤️ Реакции на комменты: {s['reactions_total']}\n"
            f"  • Сегодня: {s['reactions_today']}\n"
            f"  • По аккаунтам:\n{acc_lines}"
        )
        await event.respond(text, buttons=MAIN_KEYBOARD, parse_mode="html")

    # ── 👥 База лидов ─────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern="👥 База лидов"))
    async def cmd_leads(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        mode = "По ключевым словам 🔑" if _on(storage, S_FILTER) else "Все подряд 👥"
        total = storage.full_stats()["total_profiles"]
        text = (
            f"👥 <b>База лидов</b>\n\n"
            f"Профилей: {total}\n"
            f"Режим фильтра: {mode}\n\n"
            f"Команды:\n"
            f"/add_kw слово — добавить ключевое слово\n"
            f"/remove_kw слово — удалить\n"
            f"/keywords — список слов\n"
            f"/toggle_mode — переключить режим"
        )
        await event.respond(text, buttons=MAIN_KEYBOARD, parse_mode="html")

    # ── ⚙️ Действия ───────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern="⚙️ Действия"))
    async def cmd_actions(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        s_on = _on(storage, S_STORIES)
        r_on = _on(storage, S_REACTIONS)
        c_on = _on(storage, S_REACTOR)
        text = (
            f"⚙️ <b>Действия</b>\n\n"
            f"{_icon(s_on)} Просмотры сторис\n"
            f"{_icon(r_on)} Реакции на сторис\n"
            f"{_icon(c_on)} Лайки на комменты\n\n"
            f"Для переключения нажми кнопку:"
        )
        buttons = [
            [Button.inline(f"{_icon(s_on)} Просмотры сторис",  b"toggle_stories"),
             Button.inline(f"{_icon(r_on)} Реакции сторис",    b"toggle_reactions")],
            [Button.inline(f"{_icon(c_on)} Лайки комментов",   b"toggle_reactor")],
        ]
        await event.respond(text, buttons=buttons, parse_mode="html")

    # ── 📱 Аккаунты ───────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern="📱 Аккаунты"))
    async def cmd_accounts(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        accounts = storage.get_enabled_accounts()
        if accounts:
            lines = "\n".join(f"  • {a['label'] or a['phone']} ({a['phone']})" for a in accounts)
            text = f"📱 <b>Аккаунты ({len(accounts)} шт.)</b>\n\n{lines}\n\n<i>Добавить новый:\n.venv/bin/python add_account.py +номер</i>"
        else:
            text = "📱 <b>Аккаунтов нет</b>\n\n<i>Добавить:\n.venv/bin/python add_account.py +номер</i>"
        await event.respond(text, buttons=MAIN_KEYBOARD, parse_mode="html")

    # ── 📡 Чаты ───────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern="📡 Чаты"))
    async def cmd_chats(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        rows = storage._conn.execute(
            "SELECT handle, label, category FROM chats WHERE enabled=1"
        ).fetchall()
        if rows:
            lines = "\n".join(
                f"  • {r['label'] or r['handle']} → {r['category'] or 'общая'}"
                for r in rows
            )
            text = f"📡 <b>Активные чаты ({len(rows)})</b>\n\n{lines}\n\n<i>Добавить: /add_chat -100xxx Название Категория\nУбрать: /remove_chat -100xxx</i>"
        else:
            text = "📡 <b>Чатов нет</b>\n\n<i>Добавить: /add_chat -100xxx Название Категория</i>"
        await event.respond(text, buttons=MAIN_KEYBOARD, parse_mode="html")

    # ── 📤 Экспорт CSV ────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern="📤 Экспорт CSV"))
    async def cmd_export(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        await event.respond("⏳ Готовлю файл...", buttons=MAIN_KEYBOARD)
        rows = storage._conn.execute(
            "SELECT full_name, username, profile_url, about, category, source_chat, created_at "
            "FROM profiles ORDER BY category, created_at"
        ).fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Имя", "Username", "Ссылка", "Описание", "Категория", "Источник", "Дата"])
        for r in rows:
            w.writerow([
                r["full_name"],
                f"@{r['username']}" if r["username"] else "",
                r["profile_url"],
                r["about"],
                r["category"] or "общая",
                r["source_chat"],
                r["created_at"][:10],
            ])
        buf.seek(0)
        await bot.send_file(
            event.chat_id,
            file=buf.read().encode("utf-8"),
            caption=f"📤 Экспорт: {len(rows)} профилей",
            force_document=True,
            file_name="leads.csv",
        )

    def _fl_page(chat_id: int) -> tuple[str, list]:
        """Возвращает текст и кнопки страницы фриланса."""
        try:
            from freelancer_bot.storage import Storage as FStorage
            from pathlib import Path
            fs = FStorage(Path("data/leads.sqlite3"))
            sources = fs.get_enabled_sources()
            kw = fs.get_active_keywords()
            sw = fs.get_stop_words()
            stats = fs.stats()
            subs = fs.subscribers()
            is_active = chat_id in subs
            fs.close()
            src_list = "\n".join(f"  • {s.title}" for s in sources[:8]) or "  нет"
            kw_list = ", ".join(list(kw.keys())[:12]) + ("..." if len(kw) > 12 else "")
            status = "✅ Активен" if is_active else "⏸ На паузе"
            text = (
                f"🎯 <b>Парсер фриланса</b>  {status}\n\n"
                f"📡 Источников: {len(sources)}\n{src_list}\n\n"
                f"🔑 Ключевых слов: {len(kw)}\n{kw_list}\n\n"
                f"🚫 Стоп-слов: {len(sw)}\n"
                f"📊 Лидов найдено: {stats['leads']}"
            )
            toggle_btn = Button.inline("⏸ Пауза" if is_active else "▶️ Запустить", b"fl_toggle")
        except Exception as e:
            text = f"🎯 <b>Парсер фриланса</b>\n\n<i>Ошибка: {e}</i>"
            toggle_btn = Button.inline("▶️ Запустить", b"fl_toggle")

        buttons = [
            [toggle_btn],
            [Button.inline("📡 Источники",       b"fl_sources"),
             Button.inline("🔑 Ключевые слова",  b"fl_keywords")],
        ]
        return text, buttons

    def _fl_sources_page() -> tuple[str, list]:
        try:
            from freelancer_bot.storage import Storage as FStorage
            from pathlib import Path
            fs = FStorage(Path("data/leads.sqlite3"))
            all_sources = fs.get_sources()
            fs.close()
            lines = "\n".join(
                f"  {'✅' if en else '❌'} {s.title} (@{s.handle})"
                for s, en in all_sources
            ) or "  нет источников"
            text = f"📡 <b>Источники фриланса</b>\n\n{lines}\n\n<i>/fl_add @handle Название — добавить\n/fl_remove @handle — убрать</i>"
        except Exception as e:
            text = f"<i>Ошибка: {e}</i>"
        buttons = [[Button.inline("← Назад", b"fl_back")]]
        return text, buttons

    def _fl_keywords_page() -> tuple[str, list]:
        try:
            from freelancer_bot.storage import Storage as FStorage
            from pathlib import Path
            fs = FStorage(Path("data/leads.sqlite3"))
            kw = fs.get_active_keywords()
            sw = fs.get_stop_words()
            fs.close()
            kw_text = ", ".join(list(kw.keys()))
            sw_text = ", ".join(sw) if sw else "нет"
            text = (
                f"🔑 <b>Ключевые слова ({len(kw)})</b>\n\n{kw_text}\n\n"
                f"🚫 <b>Стоп-слова ({len(sw)})</b>\n{sw_text}\n\n"
                f"<i>/fl_add_kw слово — добавить\n/fl_remove_kw слово — убрать</i>"
            )
        except Exception as e:
            text = f"<i>Ошибка: {e}</i>"
        buttons = [[Button.inline("← Назад", b"fl_back")]]
        return text, buttons

    # ── 🎯 Фриланс ────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern="🎯 Фриланс"))
    async def cmd_freelance(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        text, buttons = _fl_page(event.chat_id)
        await event.respond(text, buttons=buttons, parse_mode="html")

    @bot.on(events.NewMessage(pattern=r"/fl_add (@\S+) (.+)"))
    async def cmd_fl_add(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        try:
            from freelancer_bot.storage import Storage as FStorage
            from pathlib import Path
            handle = event.pattern_match.group(1).lstrip("@")
            title = event.pattern_match.group(2)
            fs = FStorage(Path("data/leads.sqlite3"))
            fs.add_source(handle, title)
            fs.close()
            await event.respond(f"✅ Источник @{handle} добавлен", buttons=MAIN_KEYBOARD)
        except Exception as e:
            await event.respond(f"❌ Ошибка: {e}", buttons=MAIN_KEYBOARD)

    @bot.on(events.NewMessage(pattern=r"/fl_remove (@\S+)"))
    async def cmd_fl_remove(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        try:
            from freelancer_bot.storage import Storage as FStorage
            from pathlib import Path
            handle = event.pattern_match.group(1).lstrip("@")
            fs = FStorage(Path("data/leads.sqlite3"))
            fs.remove_source(handle)
            fs.close()
            await event.respond(f"🗑 @{handle} удалён", buttons=MAIN_KEYBOARD)
        except Exception as e:
            await event.respond(f"❌ Ошибка: {e}", buttons=MAIN_KEYBOARD)

    @bot.on(events.NewMessage(pattern=r"/fl_add_kw (.+)"))
    async def cmd_fl_add_kw(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        try:
            from freelancer_bot.storage import Storage as FStorage
            from pathlib import Path
            word = event.pattern_match.group(1).strip().lower()
            fs = FStorage(Path("data/leads.sqlite3"))
            fs.add_keyword(word, weight=2, is_stop=False)
            fs.close()
            await event.respond(f"✅ «{word}» добавлено", buttons=MAIN_KEYBOARD)
        except Exception as e:
            await event.respond(f"❌ Ошибка: {e}", buttons=MAIN_KEYBOARD)

    @bot.on(events.NewMessage(pattern=r"/fl_remove_kw (.+)"))
    async def cmd_fl_remove_kw(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        try:
            from freelancer_bot.storage import Storage as FStorage
            from pathlib import Path
            word = event.pattern_match.group(1).strip().lower()
            fs = FStorage(Path("data/leads.sqlite3"))
            fs.remove_keyword(word)
            fs.close()
            await event.respond(f"🗑 «{word}» удалено", buttons=MAIN_KEYBOARD)
        except Exception as e:
            await event.respond(f"❌ Ошибка: {e}", buttons=MAIN_KEYBOARD)

    # ── Инлайн toggles ────────────────────────────────────────────────────────
    @bot.on(events.CallbackQuery())
    async def callback_handler(event: events.CallbackQuery.Event) -> None:
        if not is_admin(event.chat_id):
            await event.answer()
            return

        data = event.data
        if data == b"toggle_stories":
            val = storage.toggle_setting(S_STORIES)
            await event.answer(f"{'✅ Включено' if val else '❌ Выключено'}")
        elif data == b"toggle_reactions":
            val = storage.toggle_setting(S_REACTIONS)
            await event.answer(f"{'✅ Включено' if val else '❌ Выключено'}")
        elif data == b"toggle_reactor":
            val = storage.toggle_setting(S_REACTOR)
            await event.answer(f"{'✅ Включено' if val else '❌ Выключено'}")

        # Фриланс
        elif data == b"fl_toggle":
            try:
                from freelancer_bot.storage import Storage as FStorage
                from pathlib import Path
                fs = FStorage(Path("data/leads.sqlite3"))
                subs = fs.subscribers()
                if event.chat_id in subs:
                    fs.remove_subscriber(event.chat_id)
                    await event.answer("⏸ Фриланс на паузе")
                else:
                    fs.add_subscriber(event.chat_id)
                    await event.answer("▶️ Фриланс запущен")
                fs.close()
            except Exception as e:
                await event.answer(f"Ошибка: {e}")
            text, buttons = _fl_page(event.chat_id)
            await event.edit(text, buttons=buttons, parse_mode="html")
            return

        elif data == b"fl_sources":
            text, buttons = _fl_sources_page()
            await event.edit(text, buttons=buttons, parse_mode="html")
            return

        elif data == b"fl_keywords":
            text, buttons = _fl_keywords_page()
            await event.edit(text, buttons=buttons, parse_mode="html")
            return

        elif data == b"fl_back":
            text, buttons = _fl_page(event.chat_id)
            await event.edit(text, buttons=buttons, parse_mode="html")
            return

        # Обновляем сообщение с новыми иконками
        s_on = _on(storage, S_STORIES)
        r_on = _on(storage, S_REACTIONS)
        c_on = _on(storage, S_REACTOR)
        text = (
            f"⚙️ <b>Действия</b>\n\n"
            f"{_icon(s_on)} Просмотры сторис\n"
            f"{_icon(r_on)} Реакции на сторис\n"
            f"{_icon(c_on)} Лайки на комменты"
        )
        buttons = [
            [Button.inline(f"{_icon(s_on)} Просмотры сторис",  b"toggle_stories"),
             Button.inline(f"{_icon(r_on)} Реакции сторис",    b"toggle_reactions")],
            [Button.inline(f"{_icon(c_on)} Лайки комментов",   b"toggle_reactor")],
        ]
        await event.edit(text, buttons=buttons, parse_mode="html")

    # ── Текстовые команды ─────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern="/keywords"))
    async def cmd_keywords(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        kw = storage.get_keywords()
        sw = storage.get_stop_words()
        kw_text = ", ".join(kw[:30]) + (f"... +{len(kw)-30}" if len(kw) > 30 else "")
        sw_text = ", ".join(sw) if sw else "нет"
        await event.respond(
            f"🔑 <b>Ключевые слова ({len(kw)})</b>\n{kw_text}\n\n"
            f"🚫 <b>Стоп-слова:</b> {sw_text}",
            buttons=MAIN_KEYBOARD, parse_mode="html"
        )

    @bot.on(events.NewMessage(pattern="/toggle_mode"))
    async def cmd_toggle_mode(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        val = storage.toggle_setting(S_FILTER)
        if parser_ref:
            parser_ref[0].filter_mode = val
        mode = "По ключевым словам 🔑" if val else "Все подряд 👥"
        await event.respond(f"✅ Режим: <b>{mode}</b>", buttons=MAIN_KEYBOARD, parse_mode="html")

    @bot.on(events.NewMessage(pattern=r"/add_chat (.+)"))
    async def cmd_add_chat(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        parts = event.pattern_match.group(1).strip().split(None, 2)
        handle = parts[0]
        label = parts[1] if len(parts) > 1 else ""
        category = parts[2] if len(parts) > 2 else None
        storage.add_chat(handle)
        if label or category:
            storage._conn.execute(
                "UPDATE chats SET label=?, category=? WHERE handle=?",
                (label or None, category, handle)
            )
            storage._conn.commit()
        await event.respond(
            f"✅ Чат <b>{label or handle}</b> добавлен (категория: {category or 'общая'})",
            buttons=MAIN_KEYBOARD, parse_mode="html"
        )

    @bot.on(events.NewMessage(pattern=r"/remove_chat (.+)"))
    async def cmd_remove_chat(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        handle = event.pattern_match.group(1).strip()
        storage.remove_chat(handle)
        await event.respond(f"🗑 Чат {handle} отключён", buttons=MAIN_KEYBOARD)

    @bot.on(events.NewMessage(pattern=r"/add_kw (.+)"))
    async def cmd_add_kw(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        word = event.pattern_match.group(1).strip().lower()
        storage.add_keyword(word)
        await event.respond(f"✅ Ключевое слово «{word}» добавлено", buttons=MAIN_KEYBOARD)

    @bot.on(events.NewMessage(pattern=r"/remove_kw (.+)"))
    async def cmd_remove_kw(event: events.NewMessage.Event) -> None:
        if not is_admin(event.chat_id):
            return
        word = event.pattern_match.group(1).strip().lower()
        storage.remove_keyword(word)
        await event.respond(f"🗑 «{word}» удалено", buttons=MAIN_KEYBOARD)
