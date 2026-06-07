from __future__ import annotations

import asyncio
import logging
import os
import random
import time

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
DELAY_MIN = 35
DELAY_MAX = 75

# Реакции которые ставим (миксуем чтобы не выглядело как бот)
REACTIONS = ["❤️", "🔥"]

# Сколько профилей обрабатываем за один прогон с одного аккаунта
DAILY_LIMIT_PER_ACCOUNT = 40

# Минимальный интервал между прогонами (часов)
MIN_INTERVAL_HOURS = 8


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
        self.react = react

    def _last_run_file(self) -> str:
        return f"/tmp/stories_last_run_{self.account_label}"

    def _time_since_last_run(self) -> float:
        path = self._last_run_file()
        if not os.path.exists(path):
            return float("inf")
        try:
            return time.time() - float(open(path).read().strip())
        except Exception:
            return float("inf")

    def _mark_ran(self) -> None:
        with open(self._last_run_file(), "w") as f:
            f.write(str(time.time()))

    async def _safe_delay(self) -> None:
        delay = random.uniform(DELAY_MIN, DELAY_MAX)
        log.debug("[%s] Пауза %.1f сек...", self.account_label, delay)
        await asyncio.sleep(delay)

    async def _process_profile(self, user_id: int, username: str) -> bool:
        """Смотрит сторис профиля и ставит реакцию. Возвращает True если сторис были."""
        try:
            entity = await self.client.get_entity(username)
        except (UserPrivacyRestrictedError, ValueError):
            log.debug("[%s] Нет доступа к профилю @%s", self.account_label, username)
            return False
        except FloodWaitError as e:
            log.warning("[%s] FloodWait %d сек при get_entity", self.account_label, e.seconds)
            await asyncio.sleep(e.seconds + 5)
            return False
        except Exception as e:
            log.debug("[%s] Ошибка получения @%s: %s", self.account_label, username, e)
            return False

        try:
            result = await self.client(GetPeerStoriesRequest(peer=entity))
            stories = result.stories.stories if result.stories else []
        except FloodWaitError as e:
            log.warning("[%s] FloodWait %d сек при GetPeerStories", self.account_label, e.seconds)
            await asyncio.sleep(e.seconds + 5)
            return False
        except Exception as e:
            log.debug("[%s] Нет сторис у @%s: %s", self.account_label, username, e)
            return False

        if not stories:
            self.storage.mark_stories_viewed(user_id)
            return False

        story_ids = [s.id for s in stories]
        try:
            await self.client(IncrementStoryViewsRequest(peer=entity, id=story_ids))
            log.info("[%s] 👁 Просмотрел сторис @%s (%d шт.)", self.account_label, username, len(story_ids))
        except FloodWaitError as e:
            log.warning("[%s] FloodWait %d сек при IncrementViews", self.account_label, e.seconds)
            await asyncio.sleep(e.seconds + 5)
            return False
        except Exception as e:
            log.debug("[%s] Ошибка просмотра сторис @%s: %s", self.account_label, username, e)
            return False

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

        if self.react and stories:
            self.storage.log_reaction(
                chat_id=f"stories:{user_id}",
                message_id=stories[-1].id,
                account=self.account_label,
            )

        self.storage.mark_stories_viewed(user_id)
        return True

    async def run_once(self, limit: int = DAILY_LIMIT_PER_ACCOUNT) -> int:
        """
        Один прогон сторис. Пропускает если прогон уже был менее MIN_INTERVAL_HOURS назад —
        защита от повторного запуска при рестартах бота.
        """
        if self.storage.get_setting("stories_enabled", "1") == "0":
            log.info("[%s] Просмотры сторис отключены.", self.account_label)
            return 0

        elapsed = self._time_since_last_run()
        if elapsed < MIN_INTERVAL_HOURS * 3600:
            remaining_min = int((MIN_INTERVAL_HOURS * 3600 - elapsed) / 60)
            log.info("[%s] Прогон был %.1f ч назад — пропускаем. Следующий через ~%d мин.",
                     self.account_label, elapsed / 3600, remaining_min)
            return 0

        profiles = self.storage.get_profiles_for_stories(limit=limit)
        if not profiles:
            log.info("[%s] Нет профилей для просмотра сторис.", self.account_label)
            return 0

        self._mark_ran()
        log.info("[%s] Начинаю просмотр сторис: %d профилей", self.account_label, len(profiles))
        viewed = 0

        for p in profiles:
            has_stories = await self._process_profile(p["user_id"], p["username"])
            if has_stories:
                viewed += 1
            await self._safe_delay()

        log.info("[%s] Готово. Сторис были у %d из %d профилей.", self.account_label, viewed, len(profiles))
        return viewed
