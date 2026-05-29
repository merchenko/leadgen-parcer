from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return ""


@dataclass(frozen=True)
class RuntimeConfig:
    api_id: int
    api_hash: str
    bot_token: str
    target_chat_id: Optional[int]
    admin_chat_ids: frozenset[int]
    database_path: Path
    user_session_path: Path
    bot_session_path: Path
    catch_up_limit: int
    send_catch_up: bool
    log_level: str

    def is_admin(self, chat_id: int) -> bool:
        if self.target_chat_id is not None and chat_id == self.target_chat_id:
            return True
        return chat_id in self.admin_chat_ids

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        load_dotenv()

        api_id_raw = _first_env("TELEGRAM_API_ID", "API_ID")
        api_hash = _first_env("TELEGRAM_API_HASH", "API_HASH")
        bot_token = _first_env("TELEGRAM_BOT_TOKEN", "BOT_TOKEN")
        missing = [
            name
            for name, value in {
                "TELEGRAM_API_ID/API_ID": api_id_raw,
                "TELEGRAM_API_HASH/API_HASH": api_hash,
                "TELEGRAM_BOT_TOKEN/BOT_TOKEN": bot_token,
            }.items()
            if not value
        ]
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(f"Missing required environment variables: {joined}")

        try:
            api_id = int(api_id_raw)
        except ValueError as exc:
            raise RuntimeError("TELEGRAM_API_ID must be an integer") from exc

        target_chat_id_raw = _first_env("TELEGRAM_TARGET_CHAT_ID", "TARGET_USER_ID")
        target_chat_id = int(target_chat_id_raw) if target_chat_id_raw else None

        admin_ids_raw = os.getenv("ADMIN_CHAT_IDS", "")
        admin_chat_ids: frozenset[int] = frozenset(
            int(x.strip()) for x in admin_ids_raw.split(",") if x.strip().lstrip("-").isdigit()
        )

        return cls(
            api_id=api_id,
            api_hash=api_hash,
            bot_token=bot_token,
            target_chat_id=target_chat_id,
            admin_chat_ids=admin_chat_ids,
            database_path=Path(os.getenv("DATABASE_PATH", "data/leads.sqlite3")),
            user_session_path=Path(os.getenv("USER_SESSION_PATH", "sessions/freelancer_user")),
            bot_session_path=Path(os.getenv("BOT_SESSION_PATH", "sessions/freelancer_delivery_bot")),
            catch_up_limit=max(0, int(os.getenv("CATCH_UP_LIMIT", "25"))),
            send_catch_up=_bool_env("SEND_CATCH_UP", True),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
