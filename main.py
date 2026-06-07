"""
Главная точка запуска. Запускает всё параллельно:
  1. Profile Parser  — сбор базы лидов по всем чатам
  2. Comment Reactor — лайки на свежие комменты
  3. Stories Viewer  — просмотр сторис + реакции (не чаще 1 раза в 8 ч)
  4. Вечерний отчёт  — каждый день в 20:00 по Киеву
  5. Freelancer Bot  — парсер фриланс-заказов

Запуск: .venv/bin/python main.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
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

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


async def evening_report(bot: TelegramClient, storage: ProfileStorage, target_chat_id: int) -> None:
    """Каждый день в 20:00 по Киеву (17:00 UTC) шлёт сводку."""
    while True:
        now = datetime.now(timezone.utc)
        target = now.replace(hour=17, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_secs = (target - now).total_seconds()
        log.info("Вечерний отчёт через %.1f ч.", wait_secs / 3600)
        await asyncio.sleep(wait_secs)

        s = storage.full_stats()
        cat_lines = "\n".join(f"  • {k}: {v}" for k, v in sorted(s["by_category"].items()))
        acc_lines = "\n".join(f"  • {k}: {v}" for k, v in s["by_account"].items()) or "  нет данных"

        text = (
            f"🌆 <b>Вечерний отчёт за день</b>\n\n"
            f"👥 <b>База лидов: {s['total_profiles']}</b>\n{cat_lines}\n\n"
            f"👁 <b>Сторис за день:</b> {s['stories_today']} просмотров\n"
            f"❤️ <b>Реакции за день:</b> {s['reactions_today']} лайков\n\n"
            f"📊 <b>Всего накоплено:</b>\n"
            f"  • Сторис просмотрено: {s['stories_viewed']} профилей\n"
            f"  • Реакций на сторис: {s['stories_reactions']}\n"
            f"  • Реакций на комменты: {s['reactions_total']}\n\n"
            f"<b>По аккаунтам (комменты):</b>\n{acc_lines}"
        )

        try:
            await bot.send_message(target_chat_id, text, parse_mode="html")
            log.info("Вечерний отчёт отправлен.")
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
    bot_session    = Path(os.getenv("BOT_SESSION_PATH", "sessions/profile_parser_bot"))
    db_path        = Path(os.getenv("PARSER_DATABASE_PATH", "data/profiles.sqlite3"))
    filter_mode    = os.getenv("PARSER_FILTER_MODE", "true").lower() in {"1", "true", "yes"}
    history_limit  = int(os.getenv("PARSER_HISTORY_LIMIT", "500"))
    digest_interval = int(os.getenv("DIGEST_INTERVAL_SECONDS", "3600"))
    react_stories  = os.getenv("STORIES_REACT", "true").lower() in {"1", "true", "yes"}

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

    # ── Freelancer Bot ────────────────────────────────────────────────────────
    fl_bot_ref: list[LeadBot] = []
    try:
        fl_config = FreelanceConfig.from_env()
        fl_bot = LeadBot(fl_config)
        await fl_bot.attach(bot_client, user_clients[0])
        fl_bot_ref.append(fl_bot)
        log.info("Freelancer bot подключён")
    except Exception as e:
        log.warning("Freelancer bot не запущен: %s", e)

    # ── Bot Commands ──────────────────────────────────────────────────────────
    register_commands(bot_client, storage, admin_ids, parser_ref=[parsers[0]], fl_bot_ref=fl_bot_ref)

    # ── Stories Viewers + Reactors ────────────────────────────────────────────
    viewers = [
        StoriesViewer(uc, storage, acc["label"] or acc["phone"], react_stories)
        for uc, acc in zip(user_clients, accounts)
    ]
    reactors = [
        CommentReactor(uc, storage, acc["label"] or acc["phone"])
        for uc, acc in zip(user_clients, accounts)
    ]

    log.info("=" * 50)
    log.info("🚀 Запуск. Аккаунтов: %d | Чатов: %d", len(accounts), len(chats))
    log.info("=" * 50)

    # ── Сканирование истории ──────────────────────────────────────────────────
    async def run_history_scan() -> None:
        if not chats:
            log.info("Чатов нет.")
            return
        for idx, chat in enumerate(chats):
            parser = parsers[idx % len(parsers)]
            await parser.scan_history(chat, limit=history_limit)
            if idx < len(chats) - 1:
                await asyncio.sleep(30)
        parsers[0].start_listener(chats)

    # ── Цикл сторис и реакций ─────────────────────────────────────────────────
    # viewer.run_once() сам проверяет интервал (MIN_INTERVAL_HOURS=8) — можно
    # вызывать часто, лишний вызов просто пропустится без действий.
    async def run_account_loop(viewer: StoriesViewer, reactor: CommentReactor, start_delay: int) -> None:
        if start_delay:
            await asyncio.sleep(start_delay)
        while True:
            results = await asyncio.gather(
                viewer.run_once(),
                reactor.run_once(chats),
                return_exceptions=True,
            )
            for i, res in enumerate(results):
                if isinstance(res, Exception):
                    name = ["viewer", "reactor"][i]
                    log.error("Ошибка в %s account loop: %s", name, res, exc_info=res)
            await asyncio.sleep(45 * 60)  # каждые 45 мин — viewer пропустит если ещё рано

    # ── Shutdown ──────────────────────────────────────────────────────────────
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    async def wait_for_stop() -> None:
        await stop_event.wait()

    # ── Запускаем всё ─────────────────────────────────────────────────────────
    tasks = await asyncio.gather(
        run_history_scan(),
        parsers[0].run_digest_loop(digest_interval),
        evening_report(bot_client, storage, target_chat_id),
        *[uc.run_until_disconnected() for uc in user_clients],
        bot_client.run_until_disconnected(),
        *[
            run_account_loop(viewer, reactor, i * 1800)
            for i, (viewer, reactor) in enumerate(zip(viewers, reactors))
        ],
        wait_for_stop(),
        return_exceptions=True,
    )

    # Логируем все необработанные исключения из задач
    task_names = (
        ["history_scan", "digest_loop", "evening_report"]
        + [f"user_client_{i}" for i in range(len(user_clients))]
        + ["bot_client"]
        + [f"account_loop_{i}" for i in range(len(viewers))]
        + ["stop_signal"]
    )
    for name, result in zip(task_names, tasks):
        if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
            log.error("Задача '%s' завершилась с ошибкой: %s", name, result, exc_info=result)

    storage.close()
    log.info("Shutdown завершён.")


if __name__ == "__main__":
    asyncio.run(main())
