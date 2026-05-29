import asyncio, os, json, sys
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from dotenv import load_dotenv

load_dotenv()

api_id   = int(os.getenv("TELEGRAM_API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH")
session  = "sessions/freelancer_user"

async def main():
    code = sys.argv[1] if len(sys.argv) > 1 else input("Введи код: ")

    with open(".auth_state.json") as f:
        data = json.load(f)

    client = TelegramClient(session, api_id, api_hash)
    await client.connect()

    try:
        await client.sign_in(data["phone"], code, phone_code_hash=data["phone_code_hash"])
    except SessionPasswordNeededError:
        password = input("Введи пароль 2FA: ")
        await client.sign_in(password=password)

    me = await client.get_me()
    print(f"Авторизован как: {me.first_name} (@{me.username}) id={me.id}")
    os.remove(".auth_state.json")
    await client.disconnect()
    print("Готово. Сессия сохранена. Запускай бот командой: .venv/bin/python -m freelancer_bot")

asyncio.run(main())
