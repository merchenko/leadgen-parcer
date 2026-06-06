"""
Добавление нового аккаунта в profile_parser.

Шаг 1 — запрос кода:
    .venv/bin/python add_account.py +37368XXXXXX

Шаг 2 — подтверждение кода:
    .venv/bin/python add_account.py +37368XXXXXX 12345
    # Если есть 2FA пароль:
    .venv/bin/python add_account.py +37368XXXXXX 12345 mypassword
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from profile_parser.storage import ProfileStorage

load_dotenv()

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
STATE_FILE = ".auth_state_new.json"


async def request_code(phone: str) -> None:
    session_path = f"sessions/account_{phone.lstrip('+')}"
    Path("sessions").mkdir(exist_ok=True)

    client = TelegramClient(session_path, API_ID, API_HASH)
    await client.connect()
    result = await client.send_code_request(phone)

    with open(STATE_FILE, "w") as f:
        json.dump({
            "phone": phone,
            "session_path": session_path,
            "phone_code_hash": result.phone_code_hash,
        }, f)

    print(f"✅ Код отправлен на {phone}")
    print(f"Теперь запусти:")
    print(f"  .venv/bin/python add_account.py {phone} КОД")
    await client.disconnect()


async def confirm_code(phone: str, code: str, password: str = "") -> None:
    with open(STATE_FILE) as f:
        state = json.load(f)

    assert state["phone"] == phone, "Номер не совпадает с тем для которого запрашивали код"

    session_path = state["session_path"]
    client = TelegramClient(session_path, API_ID, API_HASH)
    await client.connect()

    try:
        await client.sign_in(phone, code, phone_code_hash=state["phone_code_hash"])
    except SessionPasswordNeededError:
        if not password:
            password = input("Введи пароль 2FA: ").strip()
        await client.sign_in(password=password)

    me = await client.get_me()
    label = f"{me.first_name or ''} @{me.username or me.id}".strip()
    print(f"✅ Авторизован как: {label}")

    # Сохраняем в базу
    storage = ProfileStorage(Path("data/profiles.sqlite3"))
    added = storage.add_account(phone, session_path, label)
    storage.close()

    if added:
        print(f"✅ Аккаунт {phone} добавлен в базу")
        print(f"   Сессия: {session_path}")
    else:
        print(f"⚠️  Аккаунт {phone} уже был в базе")

    os.remove(STATE_FILE)
    await client.disconnect()


async def main() -> None:
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        return

    phone = args[0]

    if len(args) == 1:
        await request_code(phone)
    elif len(args) >= 2:
        code = args[1]
        password = args[2] if len(args) >= 3 else ""
        await confirm_code(phone, code, password)


asyncio.run(main())
