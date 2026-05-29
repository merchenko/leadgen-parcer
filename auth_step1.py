import asyncio, os, json
from telethon import TelegramClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

api_id   = int(os.getenv("TELEGRAM_API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH")
phone    = os.getenv("TELEGRAM_PHONE") or input("Номер телефона (с +): ").strip()
session  = os.getenv("USER_SESSION_PATH", "sessions/freelancer_user")

Path("sessions").mkdir(exist_ok=True)

async def main():
    client = TelegramClient(session, api_id, api_hash)
    await client.connect()
    result = await client.send_code_request(phone)
    data = {"phone": phone, "phone_code_hash": result.phone_code_hash}
    with open(".auth_state.json", "w") as f:
        json.dump(data, f)
    print("Код отправлен на", phone)
    print("Жди SMS/Telegram-код и передай его в auth_step2.py")
    await client.disconnect()

asyncio.run(main())
