from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from datetime import datetime, timezone
from enum import Enum

from dotenv import load_dotenv
from telethon import TelegramClient, Button, events
from telethon.errors import RPCError
from telethon.tl.custom.message import Message
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault

from .config import RuntimeConfig
from .filters import KEYWORDS as DEFAULT_KEYWORDS, STOP_WORDS as DEFAULT_STOP_WORDS, match_text_dynamic
from .formatting import format_lead
from .sources import SOURCES as DEFAULT_SOURCES, Source
from .storage import LeadRecord, Storage
from .summarizer import summarize


LOGGER = logging.getLogger("freelancer_bot")


class AwaitState(Enum):
    ADD_SOURCE     = "add_source"
    REMOVE_SOURCE  = "remove_source"
    ADD_KEYWORD    = "add_keyword"
    REMOVE_KEYWORD = "remove_keyword"


MAIN_KEYBOARD = [
    [Button.text("📋 Открыть меню")],
]

# Тексты кнопок — handle_input игнорирует их чтобы не конфликтовать с кнопочными хендлерами
_BUTTON_TEXTS = frozenset({
    "📋 Открыть меню",
    "📊 Статус", "📡 Источники",
    "🔑 Ключевые слова", "🧪 Тест фильтра",
    "➕ Источник", "➖ Источник",
    "➕ Ключ-слово", "➖ Ключ-слово",
    "⏸ Пауза", "▶️ Возобновить",
})


class LeadBot:
    def __init__(self, config: RuntimeConfig):
        self.config = config
        config.user_session_path.parent.mkdir(parents=True, exist_ok=True)
        config.bot_session_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage = Storage(config.database_path)
        self._awaiting: dict[int, AwaitState] = {}

        self.storage.seed_sources([(s.handle, s.title) for s in DEFAULT_SOURCES if s.enabled])
        self.storage.seed_keywords(DEFAULT_KEYWORDS, DEFAULT_STOP_WORDS)

        self.user_client = TelegramClient(
            str(config.user_session_path), config.api_id, config.api_hash,
        )
        self.bot_client = TelegramClient(
            str(config.bot_session_path), config.api_id, config.api_hash,
        )

    def _get_admin_chat_id(self, event: events.NewMessage.Event) -> int | None:
        chat_id = int(event.chat_id)
        return chat_id if self.config.is_admin(chat_id) else None

    async def attach(self, bot_client: TelegramClient, user_client: TelegramClient) -> None:
        """Присоединяется к внешним клиентам — не создаёт свои."""
        self.bot_client = bot_client
        self.user_client = user_client
        self._register_handlers()
        if self.config.target_chat_id is not None:
            self.storage.add_subscriber(self.config.target_chat_id)
        active = await self._register_source_handlers()
        LOGGER.info("Freelancer: мониторю %s источников", len(active))
        if self.config.send_catch_up and self.config.catch_up_limit > 0:
            await self._catch_up(active)

    async def run(self) -> None:
        self._register_handlers()

        await self.user_client.start()
        await self.bot_client.start(bot_token=self.config.bot_token)

        await self._set_telegram_commands()

        if self.config.target_chat_id is not None:
            self.storage.add_subscriber(self.config.target_chat_id)

        active = await self._register_source_handlers()
        LOGGER.info("Monitoring %s Telegram sources", len(active))

        if self.config.send_catch_up and self.config.catch_up_limit > 0:
            await self._catch_up(active)

        await self._wait_until_stopped()

    async def shutdown(self) -> None:
        await self.user_client.disconnect()
        await self.bot_client.disconnect()
        self.storage.close()

    async def _set_telegram_commands(self) -> None:
        try:
            await self.bot_client(SetBotCommandsRequest(
                scope=BotCommandScopeDefault(),
                lang_code="",
                commands=[
                    BotCommand("start",    "▶️ Подписаться на лиды"),
                    BotCommand("stop",     "⏸ Остановить уведомления"),
                    BotCommand("status",   "📊 Статистика бота"),
                    BotCommand("sources",  "📡 Активные каналы"),
                    BotCommand("keywords", "🔑 Ключевые слова"),
                    BotCommand("test",     "🧪 Проверить текст фильтром"),
                ],
            ))
        except Exception as exc:
            LOGGER.warning("Could not set bot commands: %s", exc)

    def _register_handlers(self) -> None:

        @self.bot_client.on(events.NewMessage(pattern=r"^📋 Открыть меню"))
        async def open_menu(event: events.NewMessage.Event) -> None:
            if self._get_admin_chat_id(event) is None:
                return
            await event.respond("/menu")

        @self.bot_client.on(events.NewMessage(pattern=r"^(/start|▶️ Возобновить)"))
        async def start(event: events.NewMessage.Event) -> None:
            if (chat_id := self._get_admin_chat_id(event)) is None:
                return
            self._awaiting.pop(chat_id, None)
            self.storage.add_subscriber(chat_id)
            stats = self.storage.stats()
            await event.respond(
                "✅ <b>Бот активен</b> — лиды идут!\n\n"
                f"📡 Источников: <b>{len(self.storage.get_enabled_sources())}</b>\n"
                f"📥 Лидов в базе: <b>{stats['leads']}</b>",
                parse_mode="html", buttons=MAIN_KEYBOARD,
            )

        @self.bot_client.on(events.NewMessage(pattern=r"^(/stop|⏸ Пауза)"))
        async def stop(event: events.NewMessage.Event) -> None:
            if (chat_id := self._get_admin_chat_id(event)) is None:
                return
            self._awaiting.pop(chat_id, None)
            self.storage.remove_subscriber(chat_id)
            await event.respond(
                "⏸ <b>Пауза.</b> Нажми <b>▶️ Возобновить</b> чтобы включить снова.",
                parse_mode="html", buttons=MAIN_KEYBOARD,
            )

        @self.bot_client.on(events.NewMessage(pattern=r"^(/status|📊 Статус)"))
        async def status(event: events.NewMessage.Event) -> None:
            if (chat_id := self._get_admin_chat_id(event)) is None:
                return
            self._awaiting.pop(chat_id, None)
            stats = self.storage.stats()
            subscribed = chat_id in self.storage.subscribers()
            kw = self.storage.get_active_keywords()
            sw = self.storage.get_stop_words()
            await event.respond(
                f"📊 <b>Статус бота</b>\n\n"
                f"{'🟢 Активен' if subscribed else '🔴 На паузе'}\n"
                f"📡 Источников: <b>{len(self.storage.get_enabled_sources())}</b>\n"
                f"🔑 Ключевых слов: <b>{len(kw)}</b> | стоп-слов: <b>{len(sw)}</b>\n"
                f"📥 Лидов в базе: <b>{stats['leads']}</b>\n"
                f"🕐 Ожидают отправки: <b>{stats['pending']}</b>",
                parse_mode="html", buttons=MAIN_KEYBOARD,
            )

        @self.bot_client.on(events.NewMessage(pattern=r"^(/sources|📡 Источники)"))
        async def sources_cmd(event: events.NewMessage.Event) -> None:
            if (chat_id := self._get_admin_chat_id(event)) is None:
                return
            self._awaiting.pop(chat_id, None)
            srcs = self.storage.get_enabled_sources()
            lines = [
                f"{i}. <b>{s.title}</b> — <a href=\"https://t.me/{s.handle.lstrip('@')}\">{s.handle}</a>"
                for i, s in enumerate(srcs, 1)
            ]
            await event.respond(
                f"📡 <b>Активные источники ({len(srcs)})</b>\n\n" + "\n".join(lines),
                parse_mode="html", link_preview=False, buttons=MAIN_KEYBOARD,
            )

        @self.bot_client.on(events.NewMessage(pattern=r"^(/keywords|🔑 Ключевые слова)"))
        async def keywords_cmd(event: events.NewMessage.Event) -> None:
            if (chat_id := self._get_admin_chat_id(event)) is None:
                return
            self._awaiting.pop(chat_id, None)
            kw = self.storage.get_active_keywords()
            sw = self.storage.get_stop_words()
            kw_text = ", ".join(f"{w}({v})" for w, v in list(kw.items())[:40])
            sw_text = ", ".join(sw[:30])
            await event.respond(
                f"🔑 <b>Ключевые слова ({len(kw)})</b>\n<i>{kw_text}</i>\n\n"
                f"🚫 <b>Стоп-слова ({len(sw)})</b>\n<i>{sw_text}</i>",
                parse_mode="html", buttons=MAIN_KEYBOARD,
            )

        @self.bot_client.on(events.NewMessage(pattern=r"^(/test|🧪 Тест фильтра)(?:\s+(.+))?"))
        async def test_filter(event: events.NewMessage.Event) -> None:
            if (chat_id := self._get_admin_chat_id(event)) is None:
                return
            self._awaiting.pop(chat_id, None)
            grp = event.pattern_match.lastindex
            text = event.pattern_match.group(2) if grp and grp >= 2 else None
            if not text:
                await event.respond(
                    "🧪 Пришли текст после команды:\n<code>/test нужен телеграм бот на Python</code>",
                    parse_mode="html", buttons=MAIN_KEYBOARD,
                )
                return
            kw = self.storage.get_active_keywords()
            sw = self.storage.get_stop_words()
            result = match_text_dynamic(text, kw, sw)
            if result.accepted:
                await event.respond(
                    f"✅ <b>Пройдёт фильтр</b>\nScore: <b>{result.score}</b>\n"
                    f"Совпало: <i>{', '.join(result.matched_keywords)}</i>",
                    parse_mode="html", buttons=MAIN_KEYBOARD,
                )
            else:
                reason = (
                    f"стоп-слова: <i>{', '.join(result.rejected_by)}</i>"
                    if result.rejected_by
                    else f"score слишком низкий: <b>{result.score}</b>"
                )
                await event.respond(
                    f"❌ <b>Не пройдёт фильтр</b>\n{reason}",
                    parse_mode="html", buttons=MAIN_KEYBOARD,
                )

        @self.bot_client.on(events.NewMessage(pattern=r"^➕ Источник$"))
        async def add_source_prompt(event: events.NewMessage.Event) -> None:
            if (chat_id := self._get_admin_chat_id(event)) is None:
                return
            self._awaiting[chat_id] = AwaitState.ADD_SOURCE
            await event.respond(
                "➕ <b>Добавить источник</b>\n\n"
                "Пришли username канала, например:\n<code>@freelancehunt</code>\n\n"
                "Или <code>отмена</code> чтобы выйти.",
                parse_mode="html",
            )

        @self.bot_client.on(events.NewMessage(pattern=r"^➖ Источник$"))
        async def remove_source_prompt(event: events.NewMessage.Event) -> None:
            if (chat_id := self._get_admin_chat_id(event)) is None:
                return
            srcs = self.storage.get_enabled_sources()
            if not srcs:
                await event.respond("Активных источников нет.", buttons=MAIN_KEYBOARD)
                return
            self._awaiting[chat_id] = AwaitState.REMOVE_SOURCE
            lines = [f"{i}. {s.handle} — {s.title}" for i, s in enumerate(srcs, 1)]
            await event.respond(
                "➖ <b>Убрать источник</b>\n\n"
                + "\n".join(lines)
                + "\n\nПришли <b>номер</b> из списка или <code>отмена</code>.",
                parse_mode="html",
            )

        @self.bot_client.on(events.NewMessage(pattern=r"^➕ Ключ-слово$"))
        async def add_kw_prompt(event: events.NewMessage.Event) -> None:
            if (chat_id := self._get_admin_chat_id(event)) is None:
                return
            self._awaiting[chat_id] = AwaitState.ADD_KEYWORD
            await event.respond(
                "➕ <b>Добавить ключевое слово</b>\n\n"
                "Формат: <code>слово вес</code> — для ключевого\n"
                "Или: <code>!слово</code> — для стоп-слова\n\n"
                "Примеры:\n<code>crm 3</code>\n<code>!дизайнер</code>\n\n"
                "Или <code>отмена</code> чтобы выйти.",
                parse_mode="html",
            )

        @self.bot_client.on(events.NewMessage(pattern=r"^➖ Ключ-слово$"))
        async def remove_kw_prompt(event: events.NewMessage.Event) -> None:
            if (chat_id := self._get_admin_chat_id(event)) is None:
                return
            self._awaiting[chat_id] = AwaitState.REMOVE_KEYWORD
            await event.respond(
                "➖ <b>Удалить слово из фильтра</b>\n\n"
                "Пришли само слово, например:\n<code>python</code>\n\n"
                "Или <code>отмена</code> чтобы выйти.",
                parse_mode="html",
            )

        @self.bot_client.on(events.NewMessage())
        async def handle_input(event: events.NewMessage.Event) -> None:
            if (chat_id := self._get_admin_chat_id(event)) is None:
                return
            state = self._awaiting.get(chat_id)
            if not state:
                return
            text = (event.raw_text or "").strip()
            # Ignore commands and keyboard button presses — handled by dedicated handlers
            if text.startswith("/") or text in _BUTTON_TEXTS:
                return
            if text.lower() == "отмена":
                self._awaiting.pop(chat_id, None)
                await event.respond("Отменено.", buttons=MAIN_KEYBOARD)
                return

            if state == AwaitState.ADD_SOURCE:
                handle = text if text.startswith("@") else f"@{text}"
                added = self.storage.add_source(handle, handle)
                self._awaiting.pop(chat_id, None)
                msg = (
                    f"✅ Источник <code>{handle}</code> добавлен.\n"
                    "Перезапусти бот чтобы он начал его слушать."
                    if added else
                    f"ℹ️ Источник <code>{handle}</code> уже был в списке — включён."
                )
                await event.respond(msg, parse_mode="html", buttons=MAIN_KEYBOARD)

            elif state == AwaitState.REMOVE_SOURCE:
                srcs = self.storage.get_enabled_sources()
                try:
                    idx = int(text) - 1
                    if not (0 <= idx < len(srcs)):
                        raise ValueError
                except ValueError:
                    await event.respond(
                        "Пришли номер из списка, например: <code>3</code>",
                        parse_mode="html",
                    )
                    return
                src = srcs[idx]
                self.storage.remove_source(src.handle)
                self._awaiting.pop(chat_id, None)
                await event.respond(
                    f"🗑 Источник <code>{src.handle}</code> отключён.",
                    parse_mode="html", buttons=MAIN_KEYBOARD,
                )

            elif state == AwaitState.ADD_KEYWORD:
                if text.startswith("!"):
                    word = text[1:].strip()
                    if not word:
                        await event.respond(
                            "Пришли слово после !, например: <code>!дизайнер</code>",
                            parse_mode="html",
                        )
                        return
                    added = self.storage.add_keyword(word, 0, is_stop=True)
                    self._awaiting.pop(chat_id, None)
                    msg = (
                        f"🚫 Стоп-слово <code>{word}</code> добавлено."
                        if added else
                        f"ℹ️ Слово <code>{word}</code> уже есть."
                    )
                else:
                    parts = text.rsplit(maxsplit=1)
                    if len(parts) == 2 and parts[1].isdigit():
                        word, weight = parts[0], int(parts[1])
                    else:
                        word, weight = text, 2
                    added = self.storage.add_keyword(word, weight, is_stop=False)
                    self._awaiting.pop(chat_id, None)
                    msg = (
                        f"✅ Ключевое слово <code>{word}</code> (вес {weight}) добавлено."
                        if added else
                        f"ℹ️ Слово <code>{word}</code> уже есть."
                    )
                await event.respond(msg, parse_mode="html", buttons=MAIN_KEYBOARD)

            elif state == AwaitState.REMOVE_KEYWORD:
                removed = self.storage.remove_keyword(text)
                self._awaiting.pop(chat_id, None)
                msg = (
                    f"🗑 Слово <code>{text}</code> удалено из фильтра."
                    if removed else
                    f"❌ Слово <code>{text}</code> не найдено."
                )
                await event.respond(msg, parse_mode="html", buttons=MAIN_KEYBOARD)

    async def _register_source_handlers(self) -> list[tuple[SourceRow, object]]:
        from .storage import SourceRow
        active: list[tuple[SourceRow, object]] = []
        for src in self.storage.get_enabled_sources():
            try:
                entity = await self.user_client.get_entity(src.handle)
            except (ValueError, RPCError) as exc:
                LOGGER.warning("Could not resolve %s: %s", src.handle, exc)
                continue
            active.append((src, entity))

            @self.user_client.on(events.NewMessage(chats=entity))
            async def on_message(event: events.NewMessage.Event, src=src) -> None:
                await self._process_message(src.handle, src.title, event.message)

        return active

    async def _catch_up(self, active_sources: list[tuple[object, object]]) -> None:
        async def fetch_channel(src, entity) -> list[tuple[datetime, object, Message]]:
            msgs: list[tuple[datetime, object, Message]] = []
            try:
                async for msg in self.user_client.iter_messages(entity, limit=self.config.catch_up_limit):
                    msgs.append((msg.date or datetime.now(timezone.utc), src, msg))
            except RPCError as exc:
                LOGGER.warning("Could not catch up %s: %s", src.handle, exc)
            return msgs

        results = await asyncio.gather(*[fetch_channel(src, entity) for src, entity in active_sources])
        buffered = sorted(
            (item for batch in results for item in batch),
            key=lambda x: x[0],
        )
        for _, src, message in buffered:
            await self._process_message(src.handle, src.title, message)

    async def _process_message(self, handle: str, title: str, message: Message) -> None:
        text = message.message or ""
        if not text.strip():
            return

        kw = self.storage.get_active_keywords()
        sw = self.storage.get_stop_words()
        match = match_text_dynamic(text, kw, sw)
        if not match.accepted:
            return

        subscribers = self.storage.subscribers()
        if not subscribers:
            LOGGER.warning("Lead matched, but no subscribers configured: %s", handle)
            return

        link = f"https://t.me/{handle.lstrip('@')}/{message.id}"
        message_date = (message.date or datetime.now(timezone.utc)).isoformat()
        src_obj = Source(handle=handle, title=title, reason="")
        lead = LeadRecord(
            source=handle,
            message_id=int(message.id),
            link=link,
            text=text,
            score=match.score,
            keywords=match.matched_keywords,
            message_date=message_date,
        )

        if not self.storage.record_or_should_retry(lead):
            return

        try:
            summary = await summarize(text)
        except Exception as exc:
            LOGGER.warning("Summarizer failed: %s", exc)
            summary = None

        body = format_lead(src_obj, lead, summary=summary)
        results = await asyncio.gather(*(
            self.bot_client.send_message(chat_id, body, parse_mode="html", link_preview=False)
            for chat_id in subscribers
        ), return_exceptions=True)

        delivered = False
        for chat_id, result in zip(subscribers, results):
            if isinstance(result, Exception):
                LOGGER.warning("Could not deliver lead to %s: %s", chat_id, result)
            else:
                delivered = True

        if delivered:
            self.storage.mark_notified(handle, int(message.id))
            LOGGER.info("Delivered lead from %s message %s", handle, message.id)

    async def _wait_until_stopped(self) -> None:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass
        await stop_event.wait()


async def run_app() -> None:
    load_dotenv(override=True)
    config = RuntimeConfig.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = LeadBot(config)
    try:
        await app.run()
    finally:
        await app.shutdown()


def cli() -> None:
    parser = argparse.ArgumentParser(description="Monitor Telegram freelance sources and deliver leads.")
    parser.add_argument("--check-filter", help="Check a text against the current keyword filter.")
    args = parser.parse_args()

    if args.check_filter:
        load_dotenv(override=True)
        result = match_text_dynamic(args.check_filter, DEFAULT_KEYWORDS, DEFAULT_STOP_WORDS)
        print(result)
        return

    asyncio.run(run_app())
