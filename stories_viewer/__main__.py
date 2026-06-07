from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

from profile_parser.storage import ProfileStorage
from .viewer import StoriesViewer, DAILY_LIMIT_PER_ACCOUNT
from .reactor import CommentReactor

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


async def main() -> None:
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    db_path = Path(os.getenv("PARSER_DATABASE_PATH", "data/profiles.sqlite3"))

    react = os.getenv("STORIES_REACT", "true").lower() in {"1", "true", "yes"}
    interval_hours = int(os.getenv("STORIES_INTERVAL_HOURS", "24"))
    limit_per_account = int(os.getenv("STORIES_LIMIT_PER_ACCOUNT", str(DAILY_LIMIT_PER_ACCOUNT)))

    storage = ProfileStorage(db_path)

    # Загружаем аккаунты из БД
    accounts = storage.get_enabled_accounts()
    if not accounts:
        # Фолбэк на дефолтную сессию
        default_session = os.getenv("USER_SESSION_PATH", "sessions/freelancer_user")
        accounts = [{"phone": "default", "session_path": default_session, "label": "default"}]
        log.info("Аккаунтов в БД нет — используем дефолтную сессию")
    else:
        log.info("Аккаунтов: %d", len(accounts))

    # Создаём клиент и viewer для каждого аккаунта
    viewers: list[StoriesViewer] = []
    clients: list[TelegramClient] = []

    for acc in accounts:
        client = TelegramClient(acc["session_path"], api_id, api_hash)
        await client.start()
        me = await client.get_me()
        log.info("Аккаунт: %s (@%s)", me.first_name, me.username)
        clients.append(client)

        viewer = StoriesViewer(
            client=client,
            storage=storage,
            account_label=acc["label"] or acc["phone"],
            react=react,
        )
        viewers.append(viewer)

    # Создаём reactor для каждого аккаунта
    chats = storage.get_enabled_chats()
    reactors: list[CommentReactor] = []
    for acc, client in zip(accounts, [c for c in clients]):
        reactor = CommentReactor(
            client=client,
            storage=storage,
            account_label=acc["label"] or acc["phone"],
        )
        reactors.append(reactor)

    log.info(
        "Запущен. Аккаунтов: %d | Сторис+реакции: %s | Лимит сторис: %d/акк | Интервал: %dч | Чатов: %d",
        len(viewers), react, limit_per_account, interval_hours, len(chats)
    )

    # Запускаем все аккаунты параллельно с небольшим сдвигом
    async def run_account(viewer: StoriesViewer, reactor: CommentReactor, delay_start: int) -> None:
        await asyncio.sleep(delay_start)
        # Запускаем сторис и реакции на комменты параллельно
        await asyncio.gather(
            viewer.run_daily_loop(interval_hours=interval_hours),
            reactor.run_daily_loop(chats=chats, interval_hours=interval_hours),
        )

    tasks = []
    for i, (viewer, reactor) in enumerate(zip(viewers, reactors)):
        # Сдвиг старта между аккаунтами — 30 мин
        start_delay = i * 1800
        tasks.append(run_account(viewer, reactor, start_delay))

    await asyncio.gather(*tasks)

    for c in clients:
        await c.disconnect()
    storage.close()


if __name__ == "__main__":
    asyncio.run(main())
