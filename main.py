"""
Главная точка запуска. Запускает всё параллельно:
  1. Profile Parser  — сбор базы лидов по всем чатам
  2. Comment Reactor — лайки на свежие комменты
  3. Stories Viewer  — просмотр сторис + реакции
  4. Утренний отчёт  — каждый день в 09:00 присылает статистику

Запуск: .venv/bin/python main.py
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

from profile_parser.bot_commands import register_commands
from profile_parser.keywords import DEFAULT_KEYWORDS, DEFAULT_STOP_WORDS
from profile_parser.parser import ProfileParser
from profile_parser.storage import ProfileStorage
from stories_viewer.viewer import StoriesViewer
from stories_viewer.reactor import CommentReactor
from freelancer_bot.app import LeadBot
from freelancer_bot.config import RuntimeConfig as FreelanceConfig
from profile_parser.registration import register_onboarding

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


async def morning_report(bot: TelegramClient, storage: ProfileStorage, target_chat_id: int) -> None:
    """Каждый день в 09:00 UTC+3 шлёт сводку в Telegram."""
    while True:
        now = datetime.now(timezone.utc)
        # 20:00 по МСК = 17:00 UTC
        target = now.replace(hour=17, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_secs = (target - now).total_seconds()
        log.info("Утренний отчёт через %.1f ч.", wait_secs / 3600)
        await asyncio.sleep(wait_secs)

        s = storage.full_stats()
        cat_lines = "\n".join(f"  • {k}: {v}" for k, v in sorted(s["by_category"].items()))
        acc_lines = "\n".join(f"  • {k}: {v}" for k, v in s["by_account"].items()) or "  нет данных"

        text = (
            f"🌆 <b>Вечерний отчёт за день</b>\n"
            f"\n"
            f"👥 <b>База лидов: {s['total_profiles']}</b>\n"
            f"{cat_lines}\n"
            f"\n"
            f"👁 <b>Сторис за ночь:</b> {s['stories_today']} просмотров\n"
            f"❤️ <b>Реакции за ночь:</b> {s['reactions_today']} лайков\n"
            f"\n"
            f"📊 <b>Всего накоплено:</b>\n"
            f"  • Сторис просмотрено: {s['stories_viewed']} профилей\n"
            f"  • Реакций на сторис: {s['stories_reactions']}\n"
            f"  • Реакций на комменты: {s['reactions_total']}\n"
            f"\n"
            f"<b>По аккаунтам (комменты):</b>\n"
            f"{acc_lines}"
        )

        try:
            await bot.send_message(target_chat_id, text, parse_mode="html")
            log.info("Утренний отчёт отправлен.")
        except Exception as e:
            log.error("Ошибка отправки отчёта: %s", e)


async def main() -> None:
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    target_chat_id = int(os.environ["TELEGRAM_TARGET_CHAT_ID"])
    admin_ids = [
        int(x) for x in os.getenv("ADMIN_CHAT_IDS", str(target_chat_id)).split(",")
        if x.strip()
    ]

    default_session = Path(os.getenv("USER_SESSION_PATH", "sessions/freelancer_user"))
    bot_session = Path(os.getenv("BOT_SESSION_PATH", "sessions/profile_parser_bot"))
    db_path = Path(os.getenv("PARSER_DATABASE_PATH", "data/profiles.sqlite3"))
    filter_mode = os.getenv("PARSER_FILTER_MODE", "true").lower() in {"1", "true", "yes"}
    history_limit = int(os.getenv("PARSER_HISTORY_LIMIT", "500"))
    digest_interval = int(os.getenv("DIGEST_INTERVAL_SECONDS", "3600"))
    react_stories = os.getenv("STORIES_REACT", "true").lower() in {"1", "true", "yes"}
    stories_interval = int(os.getenv("STORIES_INTERVAL_HOURS", "24"))

    storage = ProfileStorage(db_path)
    storage.seed_keywords(DEFAULT_KEYWORDS, DEFAULT_STOP_WORDS)

    # ── Аккаунты ──────────────────────────────────────────────────────────────
    accounts = storage.get_enabled_accounts()
    if not accounts:
        accounts = [{"phone": "default", "session_path": str(default_session), "label": "default"}]
        log.info("Используем дефолтную сессию")
    else:
        log.info("Аккаунтов: %d", len(accounts))

    # ── Клиенты ───────────────────────────────────────────────────────────────
    bot_client = TelegramClient(str(bot_session), api_id, api_hash)
    await bot_client.start(bot_token=bot_token)

    user_clients: list[TelegramClient] = []
    for acc in accounts:
        uc = TelegramClient(acc["session_path"], api_id, api_hash)
        await uc.start()
        me = await uc.get_me()
        log.info("Аккаунт: %s (@%s)", me.first_name, me.username)
        user_clients.append(uc)

    chats = storage.get_enabled_chats()

    # ── Profile Parsers ───────────────────────────────────────────────────────
    parsers = [
        ProfileParser(uc, bot_client, storage, target_chat_id, filter_mode)
        for uc in user_clients
    ]
    register_commands(bot_client, storage, admin_ids, parser_ref=[parsers[0]])

    # ── Онбординг новых пользователей ────────────────────────────────────────
    async def on_tenant_ready(tg_id: int, client: TelegramClient, t_api_id: int, t_api_hash: str) -> None:
        """Запускает stories/reactor для нового тенанта."""
        label = f"tenant_{tg_id}"
        viewer = StoriesViewer(client, storage, label, react=True)
        reactor = CommentReactor(client, storage, label)
        asyncio.create_task(viewer.run_daily_loop(interval_hours=24))
        asyncio.create_task(reactor.run_daily_loop(chats=chats, interval_hours=24))
        log.info("Тенант %d запущен", tg_id)

    register_onboarding(bot_client, storage, admin_ids, on_tenant_ready=on_tenant_ready)

    # Запускаем активных тенантов из БД при старте
    for tenant in storage.get_active_tenants():
        try:
            tc = TelegramClient(tenant["session_path"], tenant["api_id"], tenant["api_hash"])
            await tc.start()
            me = await tc.get_me()
            log.info("Тенант загружен: @%s", me.username)
            label = f"tenant_{tenant['tg_id']}"
            asyncio.create_task(
                StoriesViewer(tc, storage, label, react=True).run_daily_loop(24)
            )
            asyncio.create_task(
                CommentReactor(tc, storage, label).run_daily_loop(chats=chats, interval_hours=24)
            )
            user_clients.append(tc)
        except Exception as e:
            log.warning("Не удалось загрузить тенанта %d: %s", tenant["tg_id"], e)

    # ── Freelancer Bot (на тех же клиентах) ──────────────────────────────────
    try:
        fl_config = FreelanceConfig.from_env()
        fl_bot = LeadBot(fl_config)
        await fl_bot.attach(bot_client, user_clients[0])
        log.info("Freelancer bot подключён")
    except Exception as e:
        log.warning("Freelancer bot не запущен: %s", e)

    # ── Stories Viewers ───────────────────────────────────────────────────────
    viewers = [
        StoriesViewer(uc, storage, acc["label"] or acc["phone"], react_stories)
        for uc, acc in zip(user_clients, accounts)
    ]

    # ── Comment Reactors ──────────────────────────────────────────────────────
    reactors = [
        CommentReactor(uc, storage, acc["label"] or acc["phone"])
        for uc, acc in zip(user_clients, accounts)
    ]

    log.info("=" * 50)
    log.info("🚀 Запуск. Аккаунтов: %d | Чатов: %d", len(accounts), len(chats))
    log.info("=" * 50)

    # ── Сканирование истории (при старте, последовательно) ────────────────────
    async def run_history_scan() -> None:
        if not chats:
            log.info("Чатов нет.")
            return
        for idx, chat in enumerate(chats):
            parser = parsers[idx % len(parsers)]
            await parser.scan_history(chat, limit=history_limit)
            if idx < len(chats) - 1:
                await asyncio.sleep(30)
        # После сбора истории — listener только на первом аккаунте
        parsers[0].start_listener(chats)

    # ── Сторис и реакции с разбивкой по аккаунтам ────────────────────────────
    async def run_account_loop(viewer: StoriesViewer, reactor: CommentReactor, start_delay: int) -> None:
        await asyncio.sleep(start_delay)
        await asyncio.gather(
            viewer.run_daily_loop(interval_hours=stories_interval),
            reactor.run_daily_loop(chats=chats, interval_hours=24),
            return_exceptions=True,
        )

    # ── Собираем все задачи ───────────────────────────────────────────────────
    tasks = [
        run_history_scan(),
        parsers[0].run_digest_loop(digest_interval),
        morning_report(bot_client, storage, target_chat_id),
        *[uc.run_until_disconnected() for uc in user_clients],
        bot_client.run_until_disconnected(),
        *[
            run_account_loop(viewer, reactor, i * 1800)
            for i, (viewer, reactor) in enumerate(zip(viewers, reactors))
        ],
    ]

    await asyncio.gather(*tasks, return_exceptions=True)
    storage.close()


if __name__ == "__main__":
    asyncio.run(main())
