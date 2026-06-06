from __future__ import annotations

import asyncio
import logging
import random

from telethon import TelegramClient
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError
from telethon.tl.functions.stories import (
    GetPeerStoriesRequest,
    IncrementStoryViewsRequest,
    SendReactionRequest,
)
from telethon.tl.types import ReactionEmoji

from profile_parser.storage import ProfileStorage

log = logging.getLogger(__name__)

# Задержка между действиями (сек)
DELAY_MIN = 20
DELAY_MAX = 45

# Реакции которые ставим (миксуем чтобы не выглядело как бот)
REACTIONS = ["❤️", "🔥"]

# Сколько профилей обрабатываем за один прогон с одного аккаунта
DAILY_LIMIT_PER_ACCOUNT = 80


class StoriesViewer:
    def __init__(
        self,
        client: TelegramClient,
        storage: ProfileStorage,
        account_label: str = "default",
        react: bool = True,
    ):
        self.client = client
        self.storage = storage
        self.account_label = account_label
        self.react = react  # Ставить ли реакцию на сторис

    async def _safe_delay(self) -> None:
        delay = random.uniform(DELAY_MIN, DELAY_MAX)
        log.debug("[%s] Пауза %.1f сек...", self.account_label, delay)
        await asyncio.sleep(delay)

    async def _process_profile(self, user_id: int, username: str) -> bool:
        """
        Смотрит сторис профиля и ставит реакцию.
        Возвращает True если сторис были и мы их посмотрели.
        """
        try:
            entity = await self.client.get_entity(username)
        except (UserPrivacyRestrictedError, ValueError):
            log.debug("[%s] Нет доступа к профилю @%s", self.account_label, username)
            return False
        except FloodWaitError as e:
            log.warning("[%s] FloodWait %d сек", self.account_label, e.seconds)
            await asyncio.sleep(e.seconds + 5)
            return False
        except Exception as e:
            log.debug("[%s] Ошибка получения @%s: %s", self.account_label, username, e)
            return False

        # Получаем сторис
        try:
            result = await self.client(GetPeerStoriesRequest(peer=entity))
            stories = result.stories.stories if result.stories else []
        except Exception as e:
            log.debug("[%s] Нет сторис у @%s: %s", self.account_label, username, e)
            return False

        if not stories:
            # Сторис нет — помечаем чтобы не проверять снова сегодня
            self.storage.mark_stories_viewed(user_id)
            return False

        # Просматриваем все сторис
        story_ids = [s.id for s in stories]
        try:
            await self.client(IncrementStoryViewsRequest(
                peer=entity,
                id=story_ids,
            ))
            log.info("[%s] 👁 Просмотрел сторис @%s (%d шт.)", self.account_label, username, len(story_ids))
        except FloodWaitError as e:
            log.warning("[%s] FloodWait %d сек при просмотре", self.account_label, e.seconds)
            await asyncio.sleep(e.seconds + 5)
            return False
        except Exception as e:
            log.debug("[%s] Ошибка просмотра сторис @%s: %s", self.account_label, username, e)
            return False

        # Ставим реакцию на последнюю сторис
        if self.react and stories and self.storage.get_setting("reactions_stories_enabled", "1") == "1":
            await asyncio.sleep(random.uniform(3, 8))
            reaction = random.choice(REACTIONS)
            try:
                await self.client(SendReactionRequest(
                    peer=entity,
                    story_id=stories[-1].id,
                    reaction=ReactionEmoji(emoticon=reaction),
                ))
                log.info("[%s] %s Реакция на @%s", self.account_label, reaction, username)
            except Exception as e:
                log.debug("[%s] Не удалось поставить реакцию @%s: %s", self.account_label, username, e)

        # stories_count уже инкрементируется в mark_stories_viewed
        # если поставили реакцию — добавляем ещё +1 через reactions_log
        if self.react and stories:
            self.storage.log_reaction(
                chat_id=f"stories:{user_id}",
                message_id=stories[-1].id if stories else 0,
                account=self.account_label,
            )

        self.storage.mark_stories_viewed(user_id)
        return True

    async def run_once(self, limit: int = DAILY_LIMIT_PER_ACCOUNT) -> int:
        if self.storage.get_setting("stories_enabled", "1") == "0":
            log.info("[%s] Просмотры сторис отключены.", self.account_label)
            return 0
        """
        Один прогон: берёт limit профилей из базы и смотрит сторис.
        Возвращает количество профилей у которых были сторис.
        """
        profiles = self.storage.get_profiles_for_stories(limit=limit)
        if not profiles:
            log.info("[%s] Нет профилей для просмотра сторис.", self.account_label)
            return 0

        log.info("[%s] Начинаю просмотр сторис: %d профилей", self.account_label, len(profiles))
        viewed = 0

        for p in profiles:
            has_stories = await self._process_profile(p["user_id"], p["username"])
            if has_stories:
                viewed += 1
            await self._safe_delay()

        log.info("[%s] Готово. Сторис были у %d из %d профилей.", self.account_label, viewed, len(profiles))
        return viewed

    async def run_daily_loop(self, interval_hours: int = 24) -> None:
        """Запускает прогон раз в N часов."""
        while True:
            await self.run_once()
            log.info("[%s] Следующий прогон через %d ч.", self.account_label, interval_hours)
            await asyncio.sleep(interval_hours * 3600)
