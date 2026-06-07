from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

from .bot_commands import register_commands
from .keywords import DEFAULT_KEYWORDS, DEFAULT_STOP_WORDS  # Fix #9: вынесено в keywords.py
from .parser import ProfileParser
from .storage import ProfileStorage

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


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

    storage = ProfileStorage(db_path)
    storage.seed_keywords(DEFAULT_KEYWORDS, DEFAULT_STOP_WORDS)

    # Загружаем аккаунты из БД, фолбэк на дефолтную сессию
    accounts = storage.get_enabled_accounts()
    if not accounts:
        log.info("Аккаунтов в БД нет — используем дефолтную сессию %s", default_session)
        accounts = [{"phone": "default", "session_path": str(default_session), "label": "default"}]
    else:
        log.info("Загружено аккаунтов: %d", len(accounts))

    bot_client = TelegramClient(str(bot_session), api_id, api_hash)
    await bot_client.start(bot_token=bot_token)

    user_clients: list[TelegramClient] = []
    parsers: list[ProfileParser] = []

    for acc in accounts:
        uc = TelegramClient(acc["session_path"], api_id, api_hash)
        await uc.start()
        me = await uc.get_me()
        log.info("Аккаунт подключён: %s (@%s)", me.first_name, me.username)
        user_clients.append(uc)
        parsers.append(ProfileParser(
            user_client=uc,
            bot_client=bot_client,
            storage=storage,
            target_chat_id=target_chat_id,
            filter_mode=filter_mode,
        ))

    register_commands(bot_client, storage, admin_ids, parser_ref=[parsers[0]])

    chats = storage.get_enabled_chats()
    if chats:
        # Делим чаты между аккаунтами равномерно
        for idx, chat in enumerate(chats):
            parser = parsers[idx % len(parsers)]
            log.info("Чат %s → аккаунт %s", chat, accounts[idx % len(accounts)]["label"])
            await parser.scan_history(chat, limit=history_limit)
            if idx < len(chats) - 1:
                log.info("Пауза 30 сек перед следующим чатом...")
                await asyncio.sleep(30)

        # Fix #2: listener регистрируем ТОЛЬКО на первом парсере
        parsers[0].start_listener(chats)
    else:
        log.info("Чатов нет. Добавь через /add_chat @handle")

    log.info("Profile Parser запущен. Аккаунтов: %d. Дайджест каждые %d сек.", len(parsers), digest_interval)

    # Fix #6: return_exceptions=True — один упавший клиент не убивает всё
    await asyncio.gather(
        *[uc.run_until_disconnected() for uc in user_clients],
        bot_client.run_until_disconnected(),
        parsers[0].run_digest_loop(digest_interval),
        return_exceptions=True,
    )

    storage.close()


if __name__ == "__main__":
    asyncio.run(main())
