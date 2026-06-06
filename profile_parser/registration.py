from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from telethon import TelegramClient, Button, events
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError,
)

from .storage import ProfileStorage

log = logging.getLogger(__name__)

# Состояния регистрации
STATE_WAIT_API_ID   = "wait_api_id"
STATE_WAIT_API_HASH = "wait_api_hash"
STATE_WAIT_PHONE    = "wait_phone"
STATE_WAIT_CODE     = "wait_code"
STATE_WAIT_2FA      = "wait_2fa"

# Хранилище состояний в памяти: tg_id → {state, данные}
_sessions: dict[int, dict] = {}

WELCOME_TEXT = """👋 Привет! Это бот для автоматического продвижения в Telegram.

<b>Что он делает:</b>

🎯 <b>Лидогенерация</b>
• Парсит чаты и собирает базу целевой аудитории
• Сортирует лиды по категориям

👁 <b>Прогрев аудитории</b>
• Просматривает сторис из базы
• Ставит реакции на сторис и комменты
• Люди видят тебя → заходят на профиль → подписываются

📋 <b>Парсер фриланс-заказов</b>
• Мониторит 13+ Telegram каналов с заказами
• Фильтрует по ключевым словам
• Присылает подходящие заказы сразу в Telegram

Всё это работает <b>24/7 автоматически.</b>"""

SETUP_INTRO = """⚙️ <b>Подключение аккаунта</b>

Бот работает с твоего Telegram аккаунта — именно он будет смотреть сторис и ставить реакции.

⚠️ <b>Важно: используй отдельный аккаунт</b>
Не используй основной — тот на котором вся работа и контакты. Купи отдельную симку или виртуальный номер:
• <a href="https://sms-activate.org">sms-activate.org</a> — от $0.5 за номер
• Любой оператор в твоей стране

Зарегистрируй на него Telegram и используй его.

━━━━━━━━━━━━━━━
<b>Что понадобится:</b>

1️⃣ <b>API ID и API Hash</b>
Ключи доступа к Telegram API. Получить бесплатно:
→ <a href="https://my.telegram.org">my.telegram.org</a>
→ Войди → API development tools → создай приложение
→ Скопируй <code>App api_id</code> и <code>App api_hash</code>

2️⃣ <b>Номер телефона</b>
Номер того отдельного Telegram аккаунта

Готов? Нажми кнопку ниже 👇"""


def register_onboarding(
    bot: TelegramClient,
    storage: ProfileStorage,
    admin_ids: list[int],
    on_tenant_ready: "callable | None" = None,
) -> None:
    """Регистрирует хендлеры онбординга для новых пользователей."""

    def is_admin(tg_id: int) -> bool:
        return tg_id in admin_ids

    async def send_welcome(event) -> None:
        await event.respond(
            WELCOME_TEXT,
            parse_mode="html",
            link_preview=False,
            buttons=[
                [Button.inline("🚀 Подключить аккаунт", b"onboard_start")],
            ],
        )

    # ── /demo — тестовый прогон без реальной авторизации ─────────────────────
    @bot.on(events.NewMessage(pattern="/demo"))
    async def cmd_demo(event: events.NewMessage.Event) -> None:
        if not await is_allowed(event):
            return
        await _show_success(event, name="Алекс", phone="+79991234567")

    # ── /start для новых пользователей ────────────────────────────────────────
    # Берём из .env: ALLOWED_USERNAMES=username1,username2
    _raw = os.getenv("ALLOWED_USERNAMES", "")
    ALLOWED_USERNAMES = {u.strip().lower().lstrip("@") for u in _raw.split(",") if u.strip()}

    async def is_allowed(event) -> bool:
        if is_admin(event.chat_id):
            return False  # Admins handled in bot_commands.py
        if not ALLOWED_USERNAMES:
            return True  # Если не задано — открыт для всех
        sender = await event.get_sender()
        username = (getattr(sender, "username", None) or "").lower()
        return username in ALLOWED_USERNAMES

    @bot.on(events.NewMessage(pattern=r"^/start$"))
    async def cmd_start_new(event: events.NewMessage.Event) -> None:
        if not await is_allowed(event):
            return

        tenant = storage.get_tenant(tg_id)
        if tenant and tenant["status"] == "active":
            await event.respond(
                "✅ Твой аккаунт уже подключён и работает!\n\n"
                "Напиши /status чтобы посмотреть статистику.",
                buttons=[[Button.inline("📊 Статистика", b"tenant_status")]],
            )
            return

        await send_welcome(event)

    # ── Callback: начать онбординг ─────────────────────────────────────────────
    @bot.on(events.CallbackQuery(data=b"onboard_start"))
    async def cb_onboard_start(event: events.CallbackQuery.Event) -> None:
        if not await is_allowed(event):
            await event.answer()
            return

        _sessions[tg_id] = {"state": STATE_WAIT_API_ID}
        await event.answer()
        await event.respond(
            SETUP_INTRO,
            parse_mode="html",
            link_preview=False,
        )
        await asyncio.sleep(0.5)
        await event.respond(
            "1️⃣ <b>Введи API ID</b>\n\n"
            "<i>Это числовой код, например: <code>12345678</code></i>",
            parse_mode="html",
            buttons=[[Button.inline("❌ Отмена", b"onboard_cancel")]],
        )

    # ── Обработка текстовых сообщений в процессе регистрации ─────────────────
    @bot.on(events.NewMessage())
    async def handle_registration_input(event: events.NewMessage.Event) -> None:
        tg_id = event.chat_id
        if tg_id not in _sessions:
            return
        if not await is_allowed(event):
            return
        if event.text and event.text.startswith("/"):
            return

        session = _sessions[tg_id]
        state = session.get("state")
        text = (event.text or "").strip()

        if not text:
            return

        # ── Шаг 1: API ID ─────────────────────────────────────────────────────
        if state == STATE_WAIT_API_ID:
            if not text.isdigit():
                await event.respond(
                    "❌ API ID должен быть числом. Попробуй ещё раз:",
                    buttons=[[Button.inline("❌ Отмена", b"onboard_cancel")]],
                )
                return
            session["api_id"] = int(text)
            session["state"] = STATE_WAIT_API_HASH
            await event.respond(
                "✅ API ID принят.\n\n"
                "2️⃣ <b>Теперь введи API Hash</b>\n\n"
                "<i>Это строка из букв и цифр, например:\n"
                "<code>a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4</code></i>",
                parse_mode="html",
                buttons=[[Button.inline("❌ Отмена", b"onboard_cancel")]],
            )

        # ── Шаг 2: API Hash ───────────────────────────────────────────────────
        elif state == STATE_WAIT_API_HASH:
            if len(text) < 10:
                await event.respond(
                    "❌ API Hash слишком короткий. Скопируй его целиком с my.telegram.org:",
                    buttons=[[Button.inline("❌ Отмена", b"onboard_cancel")]],
                )
                return
            session["api_hash"] = text
            session["state"] = STATE_WAIT_PHONE
            await event.respond(
                "✅ API Hash принят.\n\n"
                "3️⃣ <b>Введи номер телефона</b>\n\n"
                "<i>Формат с кодом страны: <code>+79991234567</code></i>",
                parse_mode="html",
                buttons=[[Button.inline("❌ Отмена", b"onboard_cancel")]],
            )

        # ── Шаг 3: Номер телефона ─────────────────────────────────────────────
        elif state == STATE_WAIT_PHONE:
            phone = text if text.startswith("+") else f"+{text}"
            session["phone"] = phone
            session["state"] = STATE_WAIT_CODE

            # Создаём клиент и отправляем код
            api_id = session["api_id"]
            api_hash = session["api_hash"]
            session_path = f"sessions/tenant_{tg_id}"
            session["session_path"] = session_path

            try:
                client = TelegramClient(session_path, api_id, api_hash)
                await client.connect()
                result = await client.send_code_request(phone)
                session["phone_code_hash"] = result.phone_code_hash
                session["client"] = client
                await event.respond(
                    f"✅ Код отправлен на <code>{phone}</code>\n\n"
                    "4️⃣ <b>Введи код из Telegram</b>\n\n"
                    "<i>Он придёт в приложение Telegram на этот номер</i>",
                    parse_mode="html",
                    buttons=[[Button.inline("❌ Отмена", b"onboard_cancel")]],
                )
            except FloodWaitError as e:
                await event.respond(f"⏳ Слишком много попыток. Подожди {e.seconds} сек и попробуй снова.")
                _sessions.pop(tg_id, None)
            except Exception as e:
                await event.respond(f"❌ Ошибка при отправке кода: {e}\n\nПроверь номер и попробуй снова.")
                _sessions.pop(tg_id, None)

        # ── Шаг 4: Код из Telegram ────────────────────────────────────────────
        elif state == STATE_WAIT_CODE:
            client: TelegramClient = session.get("client")
            if not client:
                await event.respond("❌ Сессия устарела. Начни заново: /start")
                _sessions.pop(tg_id, None)
                return

            try:
                await client.sign_in(
                    session["phone"],
                    text,
                    phone_code_hash=session["phone_code_hash"],
                )
                await _finish_registration(event, tg_id, session, storage, on_tenant_ready)

            except SessionPasswordNeededError:
                session["state"] = STATE_WAIT_2FA
                await event.respond(
                    "🔐 <b>Включена двухфакторная аутентификация</b>\n\n"
                    "Введи пароль от аккаунта:",
                    parse_mode="html",
                    buttons=[[Button.inline("❌ Отмена", b"onboard_cancel")]],
                )
            except PhoneCodeInvalidError:
                await event.respond(
                    "❌ Неверный код. Попробуй ещё раз:",
                    buttons=[[Button.inline("❌ Отмена", b"onboard_cancel")]],
                )
            except PhoneCodeExpiredError:
                await event.respond("❌ Код истёк. Начни заново: /start")
                _sessions.pop(tg_id, None)
            except Exception as e:
                await event.respond(f"❌ Ошибка: {e}")
                _sessions.pop(tg_id, None)

        # ── Шаг 5: 2FA пароль ─────────────────────────────────────────────────
        elif state == STATE_WAIT_2FA:
            client: TelegramClient = session.get("client")
            try:
                await client.sign_in(password=text)
                await _finish_registration(event, tg_id, session, storage, on_tenant_ready)
            except Exception as e:
                await event.respond(f"❌ Неверный пароль: {e}\n\nПопробуй ещё раз:")

    # ── Отмена ────────────────────────────────────────────────────────────────
    @bot.on(events.CallbackQuery(data=b"onboard_cancel"))
    async def cb_cancel(event: events.CallbackQuery.Event) -> None:
        tg_id = event.chat_id
        session = _sessions.pop(tg_id, {})
        client = session.get("client")
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        await event.answer("Отменено")
        await event.respond("❌ Регистрация отменена. Напиши /start чтобы начать заново.")

    # ── Статус тенанта ────────────────────────────────────────────────────────
    @bot.on(events.CallbackQuery(data=b"tenant_status"))
    async def cb_tenant_status(event: events.CallbackQuery.Event) -> None:
        tg_id = event.chat_id
        tenant = storage.get_tenant(tg_id)
        if not tenant or tenant["status"] != "active":
            await event.answer("Аккаунт не подключён")
            return
        phone = tenant.get("phone", "—")
        await event.answer()
        await event.respond(
            f"📊 <b>Твой аккаунт</b>\n\n"
            f"📱 Номер: <code>{phone}</code>\n"
            f"✅ Статус: активен\n\n"
            f"Бот работает в фоне 24/7.",
            parse_mode="html",
        )


async def _show_success(event, name: str, phone: str) -> None:
    """Показывает финальные сообщения после успешной регистрации."""
    await event.respond(
        f"🎉 <b>Готово! Аккаунт подключён.</b>\n\n"
        f"👤 {name}\n"
        f"📱 {phone}\n\n"
        f"Бот начнёт работу в течение нескольких минут.\n"
        f"Просмотры сторис и реакции запустятся автоматически.",
        parse_mode="html",
    )

    await asyncio.sleep(1)

    await event.respond(
        "📋 <b>Парсер фриланс-заказов</b>\n\n"
        "Если хочешь получать подходящие заказы из Telegram-каналов — "
        "бот будет мониторить их и присылать тебе напрямую.\n\n"
        "Доступно два режима:\n\n"
        "📄 <b>Стандартный</b> — получаешь пост целиком, как он есть в канале. "
        "Бесплатно.\n\n"
        "✨ <b>PRO</b> — получаешь краткое саммари по каждому заказу:\n\n"
        "<blockquote>"
        "🔔 Новый лид · Pixel | Заказы для Тех-спецов\n"
        "📅 05.06.2026 16:01\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📋 Задача: Найти Senior Backend Engineer со знанием Swift, Vapor, Kafka, PostgreSQL\n"
        "💰 Бюджет: $3000\n"
        "⏱ Срок: не указан\n"
        "🔄 Тип: не указан\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📬 Контакты: @vi22rec\n\n"
        "🔗 Открыть пост →"
        "</blockquote>\n\n"
        "PRO режим платный — AI обрабатывает каждый заказ. "
        "Если интересно, напиши мне — подключу.",
        parse_mode="html",
        link_preview=False,
    )


async def _finish_registration(
    event,
    tg_id: int,
    session: dict,
    storage: ProfileStorage,
    on_tenant_ready: "callable | None",
) -> None:
    """Завершает регистрацию — сохраняет тенанта и запускает его."""
    client: TelegramClient = session["client"]
    me = await client.get_me()
    name = f"{me.first_name or ''} @{me.username or me.id}".strip()

    storage.upsert_tenant(
        tg_id,
        username=getattr(me, "username", None),
        api_id=session["api_id"],
        api_hash=session["api_hash"],
        phone=session["phone"],
        session_path=session["session_path"],
        status="active",
    )

    _sessions.pop(tg_id, None)

    await _show_success(event, name=name, phone=session["phone"])

    log.info("Новый тенант зарегистрирован: tg_id=%d, phone=%s", tg_id, session["phone"])

    # Запускаем для нового тенанта stories/reactions
    if on_tenant_ready:
        await on_tenant_ready(tg_id, client, session["api_id"], session["api_hash"])
    else:
        await client.disconnect()
