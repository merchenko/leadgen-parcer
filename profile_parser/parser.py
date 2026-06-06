from __future__ import annotations

import asyncio
import logging
import random

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import User

from .filters import match_profile
from .storage import ProfileRecord, ProfileStorage

log = logging.getLogger(__name__)

PROFILE_FETCH_DELAY_MIN = 3.0
PROFILE_FETCH_DELAY_MAX = 6.0
MAX_PROFILES_PER_RUN = 200


def _resolve_handle(chat_handle: str) -> int | str:
    """Числовой ID чата → int, username → str."""
    if str(chat_handle).lstrip("-").isdigit():
        return int(chat_handle)
    return chat_handle


def _chat_id_str(chat_handle: str) -> str:
    """Нормализованный строковый ключ для хранения в БД."""
    h = _resolve_handle(chat_handle)
    return str(h)


class ProfileParser:
    def __init__(
        self,
        user_client: TelegramClient,
        bot_client: TelegramClient,
        storage: ProfileStorage,
        target_chat_id: int,
        filter_mode: bool = True,
    ):
        self.user_client = user_client
        self.bot_client = bot_client
        self.storage = storage
        self.target_chat_id = target_chat_id
        self.filter_mode = filter_mode

    async def _safe_delay(self) -> None:
        delay = random.uniform(PROFILE_FETCH_DELAY_MIN, PROFILE_FETCH_DELAY_MAX)
        await asyncio.sleep(delay)

    def _profile_url(self, user: User) -> str:
        # Fix #10: убрал лишний async
        if user.username:
            return f"https://t.me/{user.username}"
        return f"tg://user?id={user.id}"

    async def _process_user(self, user: User, source_chat: str) -> ProfileRecord | None:
        if user.bot or user.deleted:
            return None
        if self.storage.is_seen(user.id):
            return None

        full_name = " ".join(filter(None, [user.first_name, user.last_name]))
        about = ""

        try:
            full_info = await self.user_client(GetFullUserRequest(user.id))
            about = full_info.full_user.about or ""
        except FloodWaitError as e:
            log.warning("FloodWait %d сек — ждём...", e.seconds)
            await asyncio.sleep(e.seconds + 5)
            # Продолжаем с пустым about — не пропускаем профиль
        except Exception as e:
            log.debug("Не удалось получить полный профиль %s: %s", user.id, e)

        keywords = self.storage.get_keywords()
        stop_words = self.storage.get_stop_words()

        if not match_profile(full_name, about, keywords, stop_words, self.filter_mode):
            return None

        return ProfileRecord(
            user_id=user.id,
            username=user.username,
            full_name=full_name,
            about=about,
            profile_url=self._profile_url(user),
            source_chat=source_chat,
        )

    async def flush_digest(self) -> None:
        """Отправляет дайджест новых профилей и помечает их как отправленные."""
        profiles = self.storage.get_pending_profiles()
        if not profiles:
            log.info("Нет новых профилей для отправки.")
            return

        lines = [f"👥 <b>Новые профили — {len(profiles)} шт.</b>\n"]
        for p in profiles:
            username = f"@{p.username}" if p.username else p.profile_url
            about_short = (p.about[:60] + "…") if len(p.about) > 60 else p.about
            if about_short:
                lines.append(f"• {username} — <i>{about_short}</i>")
            else:
                lines.append(f"• {username}")

        # Режем на куски по 3800 символов
        chunks: list[str] = []
        current = ""
        for line in lines:
            if len(current) + len(line) + 1 > 3800:
                chunks.append(current)
                current = line
            else:
                current += ("\n" if current else "") + line
        if current:
            chunks.append(current)

        for chunk in chunks:
            try:
                await self.bot_client.send_message(
                    self.target_chat_id,
                    chunk,
                    parse_mode="html",
                    link_preview=False,
                )
            except Exception as e:
                log.error("Ошибка отправки дайджеста: %s", e)
                return

        for p in profiles:
            self.storage.mark_notified(p.user_id)

        log.info("Дайджест отправлен: %d профилей", len(profiles))

    async def run_digest_loop(self, interval_seconds: int = 3600) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            await self.flush_digest()

    async def scan_history(self, chat_handle: str, limit: int = 500) -> int:
        log.info("Сканирую историю %s (limit=%d)...", chat_handle, limit)
        found = 0

        # Fix #4: нормализуем handle для хранения в БД
        source_key = _chat_id_str(chat_handle)

        try:
            entity = await self.user_client.get_entity(_resolve_handle(chat_handle))
        except Exception as e:
            log.error("Не удалось получить чат %s: %s", chat_handle, e)
            return 0

        seen_in_run: set[int] = set()

        async for message in self.user_client.iter_messages(entity, limit=limit):
            if not message.sender_id or message.sender_id in seen_in_run:
                continue
            seen_in_run.add(message.sender_id)

            if self.storage.is_seen(message.sender_id):
                continue

            try:
                user = await self.user_client.get_entity(message.sender_id)
            except FloodWaitError as e:
                log.warning("FloodWait %d сек — ждём...", e.seconds)
                await asyncio.sleep(e.seconds + 5)
                continue
            except Exception:
                continue

            if not isinstance(user, User):
                continue

            await self._safe_delay()

            profile = await self._process_user(user, source_key)
            if profile and self.storage.save_profile(profile):
                # Fix #1: НЕ вызываем mark_notified здесь — дайджест сам отметит
                found += 1
                log.info("✅ Найден: %s (%s)", profile.full_name, profile.profile_url)

            if found >= MAX_PROFILES_PER_RUN:
                log.info("Достигнут лимит %d профилей — останавливаем.", MAX_PROFILES_PER_RUN)
                break

        log.info("Сканирование %s завершено. Найдено: %d", chat_handle, found)
        return found

    def start_listener(self, chat_handles: list[str]) -> None:
        """
        Fix #2: listener регистрируется ОДИН раз на первом парсере.
        Не вызывать на каждом аккаунте — будут дубли.
        """
        resolved = [_resolve_handle(c) for c in chat_handles]

        @self.user_client.on(events.NewMessage(chats=resolved))
        async def handler(event: events.NewMessage.Event) -> None:
            if not event.sender_id:
                return
            try:
                user = await event.get_sender()
            except Exception:
                return
            if not isinstance(user, User):
                return

            await self._safe_delay()

            # Fix #4: правильный source_chat — числовой ID из события
            source_key = str(-1000000000000 - event.chat_id) if event.chat_id > 0 else str(event.chat_id)
            # Берём из известных чатов по entity id
            chat_id_int = getattr(event.chat, "id", event.chat_id)
            source_key = f"-100{chat_id_int}"

            profile = await self._process_user(user, source_key)
            if profile and self.storage.save_profile(profile):
                log.info("✅ Новый: %s (%s)", profile.full_name, profile.profile_url)

        log.info("Listener запущен для: %s", chat_handles)
