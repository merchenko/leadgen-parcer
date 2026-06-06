from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta

from telethon import TelegramClient
from telethon.errors import FloodWaitError, ReactionInvalidError
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji, Message

from profile_parser.storage import ProfileStorage

log = logging.getLogger(__name__)

REACTIONS = ["❤️", "🔥"]

# Задержка между реакциями (сек)
DELAY_MIN = 15
DELAY_MAX = 40

# Сколько реакций за один прогон (каждые 45 мин)
LIMIT_PER_RUN = 20

# Сколько последних сообщений смотрим в чате
MESSAGES_SCAN_LIMIT = 200

# Возраст сообщений для лайков
MAX_MESSAGE_AGE_HOURS = 48   # не старше
MIN_MESSAGE_AGE_MINUTES = 5  # не свежее (иначе выглядит как бот)


class CommentReactor:
    def __init__(
        self,
        client: TelegramClient,
        storage: ProfileStorage,
        account_label: str = "default",
    ):
        self.client = client
        self.storage = storage
        self.account_label = account_label

    async def _safe_delay(self) -> None:
        delay = random.uniform(DELAY_MIN, DELAY_MAX)
        log.debug("[%s] Пауза %.1f сек...", self.account_label, delay)
        await asyncio.sleep(delay)

    async def _react(self, chat_entity, message: Message) -> bool:
        """Ставит реакцию на сообщение. Возвращает True если успешно."""
        chat_id = str(message.peer_id.channel_id if hasattr(message.peer_id, 'channel_id') else message.chat_id)

        if self.storage.is_reacted(chat_id, message.id, self.account_label):
            return False

        reaction = random.choice(REACTIONS)
        try:
            await self.client(SendReactionRequest(
                peer=chat_entity,
                msg_id=message.id,
                reaction=[ReactionEmoji(emoticon=reaction)],
            ))
            self.storage.log_reaction(chat_id, message.id, self.account_label)
            log.info("[%s] %s → сообщение %d в чате %s", self.account_label, reaction, message.id, chat_id)
            return True
        except ReactionInvalidError:
            log.debug("[%s] Реакция недоступна для сообщения %d", self.account_label, message.id)
            return False
        except FloodWaitError as e:
            log.warning("[%s] FloodWait %d сек", self.account_label, e.seconds)
            await asyncio.sleep(e.seconds + 5)
            return False
        except Exception as e:
            log.debug("[%s] Ошибка реакции: %s", self.account_label, e)
            return False

    async def run_once(self, chats: list[str], limit: int = LIMIT_PER_RUN) -> int:
        """
        Один прогон по всем чатам.
        Собирает последние сообщения, рандомно выбирает и ставит реакции.
        Возвращает количество поставленных реакций.
        """
        if self.storage.get_setting("reactor_enabled", "1") == "0":
            log.info("[%s] Лайки на комменты отключены.", self.account_label)
            return 0

        total = 0
        me = await self.client.get_me()

        for chat_handle in chats:
            if total >= limit:
                break

            try:
                handle = int(chat_handle) if str(chat_handle).lstrip("-").isdigit() else chat_handle
                entity = await self.client.get_entity(handle)
            except Exception as e:
                log.error("[%s] Не удалось получить чат %s: %s", self.account_label, chat_handle, e)
                continue

            # Собираем свежие сообщения и группируем по постам
            now = datetime.now(timezone.utc)
            cutoff_old = now - timedelta(hours=MAX_MESSAGE_AGE_HOURS)
            cutoff_fresh = now - timedelta(minutes=MIN_MESSAGE_AGE_MINUTES)

            # post_id → список комментов
            posts: dict[int, list] = {}
            try:
                async for msg in self.client.iter_messages(entity, limit=MESSAGES_SCAN_LIMIT):
                    if msg.date and msg.date < cutoff_old:
                        break
                    # Слишком свежие — пропускаем
                    if msg.date and msg.date > cutoff_fresh:
                        continue
                    if not msg.sender_id or msg.sender_id == me.id:
                        continue
                    if not msg.text:
                        continue
                    chat_id = str(getattr(msg.peer_id, 'channel_id', msg.chat_id))
                    if self.storage.is_reacted(chat_id, msg.id, self.account_label):
                        continue
                    # Группируем по reply_to (пост) или по самому сообщению
                    post_id = msg.reply_to.reply_to_msg_id if msg.reply_to else msg.id
                    posts.setdefault(post_id, []).append(msg)
            except Exception as e:
                log.error("[%s] Ошибка чтения чата %s: %s", self.account_label, chat_handle, e)
                continue

            if not posts:
                log.info("[%s] Нет свежих сообщений для реакций в %s", self.account_label, chat_handle)
                continue

            # Из каждого поста берём строго 1 случайный коммент
            to_react = []
            for post_id, post_comments in posts.items():
                # Если уже лайкали хоть один коммент под этим постом — пропускаем весь пост
                already = any(
                    self.storage.is_reacted(
                        str(getattr(m.peer_id, 'channel_id', m.chat_id)),
                        m.id,
                        self.account_label
                    )
                    for m in post_comments
                )
                if already:
                    continue
                to_react.append(random.choice(post_comments))

            # Перемешиваем финальный список и ограничиваем лимитом
            random.shuffle(to_react)
            remaining = limit - total
            to_react = to_react[:remaining]

            log.info("[%s] Чат %s — %d постов, ставим реакции на %d комментов",
                     self.account_label, chat_handle, len(posts), len(to_react))

            for msg in to_react:
                success = await self._react(entity, msg)
                if success:
                    total += 1
                await self._safe_delay()

        log.info("[%s] Прогон завершён. Реакций поставлено: %d", self.account_label, total)
        return total

    async def run_daily_loop(self, chats: list[str], interval_hours: int = 24) -> None:
        """Проверяет свежие посты каждые 45 минут."""
        interval_minutes = 45
        while True:
            await self.run_once(chats)
            log.info("[%s] Следующая проверка через %d мин.", self.account_label, interval_minutes)
            await asyncio.sleep(interval_minutes * 60)
