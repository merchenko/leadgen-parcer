# Telegram Freelance Lead Bot

Telegram Freelance Lead Bot — это небольшой Python-бот, который мониторит выбранные Telegram-каналы с фриланс-заказами и отправляет подходящие лиды в вашего Telegram-бота.

Проект работает без искусственного интеллекта: фильтрация сделана через понятные ключевые слова, стоп-слова и score, которые можно редактировать прямо в коде.

## Что умеет бот

- Читает публичные Telegram-каналы и группы через вашу пользовательскую Telegram-сессию.
- Фильтрует сообщения по ключевым словам и стоп-словам.
- Сохраняет обработанные лиды в SQLite, чтобы не присылать дубли.
- Отправляет найденные заказы в вашего Telegram-бота.
- Позволяет подписать или отписать чат командами `/start` и `/stop`.
- Поддерживает команды `/status`, `/sources`, `/keywords` и `/test`.

## Как это работает

В проекте используются два разных Telegram API:

- **Telegram user API** (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`) читает каналы и группы, к которым есть доступ у вашего Telegram-аккаунта.
- **Telegram Bot API** (`TELEGRAM_BOT_TOKEN`) отправляет найденные лиды вам в Telegram.

Важно: одного bot token недостаточно, чтобы читать произвольные Telegram-каналы. Для мониторинга нужна пользовательская Telegram-сессия, а bot token нужен только для доставки уведомлений.

## Что понадобится

- Python 3.10+
- Telegram-аккаунт
- Telegram API ID и API Hash с <https://my.telegram.org>
- Токен Telegram-бота от [@BotFather](https://t.me/BotFather)

## Быстрый старт

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

Откройте `.env` и заполните:

```bash
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_BOT_TOKEN=123456:bot_token
```

Запустите:

```bash
python -m freelancer_bot
```

При первом запуске Telethon может попросить номер телефона и код входа из Telegram. После запуска откройте своего Telegram-бота и отправьте ему `/start`, чтобы подписать чат на уведомления.

## Как получить ключи Telegram

### API ID и API Hash

1. Откройте <https://my.telegram.org>.
2. Войдите по номеру телефона.
3. Перейдите в **API development tools**.
4. Создайте приложение.
5. Скопируйте `api_id` и `api_hash` в `.env`.

### Bot token

1. Откройте [@BotFather](https://t.me/BotFather).
2. Отправьте `/newbot`.
3. Следуйте инструкциям BotFather.
4. Скопируйте выданный токен в `TELEGRAM_BOT_TOKEN`.

## Настройка

### Источники

Список Telegram-каналов редактируется в [freelancer_bot/sources.py](freelancer_bot/sources.py).

Пример источника:

```python
Source("@freelansim_ru", "Хабр Фриланс", "заказы по фрилансу и разработке")
```

Если источник перестал открываться или Telegram не может его найти, удалите его из списка или поставьте `enabled=False`.

### Ключевые слова и стоп-слова

Фильтр редактируется в [freelancer_bot/filters.py](freelancer_bot/filters.py).

Главные настройки:

- `KEYWORDS` — слова и фразы, которые увеличивают score лида.
- `STOP_WORDS` — слова и фразы, которые сразу отклоняют сообщение.
- `MIN_SCORE` — минимальный score, при котором сообщение отправляется в бот.

Пример:

```python
KEYWORDS = {
    "telegram bot": 5,
    "тг бот": 5,
    "парсер": 4,
    "python": 2,
}

STOP_WORDS = [
    "smm",
    "казино",
    "ставки",
]
```

Проверить фильтр можно без подключения к Telegram:

```bash
python -m freelancer_bot --check-filter "Нужно разработать телеграм бот на Python"
```

## Переменные окружения

| Переменная | Обязательная | Описание |
|---|---:|---|
| `TELEGRAM_API_ID` | да | API ID с <https://my.telegram.org> |
| `TELEGRAM_API_HASH` | да | API hash с <https://my.telegram.org> |
| `TELEGRAM_BOT_TOKEN` | да | Токен бота от [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_TARGET_CHAT_ID` | нет | Chat ID для автоматической подписки. Если не указано, отправьте `/start` боту. |
| `DATABASE_PATH` | нет | Путь к SQLite-базе. По умолчанию: `data/leads.sqlite3` |
| `USER_SESSION_PATH` | нет | Путь к пользовательской Telethon-сессии. По умолчанию: `sessions/freelancer_user` |
| `BOT_SESSION_PATH` | нет | Путь к bot-сессии Telethon. По умолчанию: `sessions/freelancer_delivery_bot` |
| `CATCH_UP_LIMIT` | нет | Сколько последних сообщений проверять в каждом источнике при запуске. По умолчанию: `25` |
| `SEND_CATCH_UP` | нет | Проверять ли свежие сообщения при запуске. По умолчанию: `true` |
| `LOG_LEVEL` | нет | Уровень логов Python. По умолчанию: `INFO` |

Старые имена `API_ID`, `API_HASH`, `BOT_TOKEN` и `TARGET_USER_ID` тоже поддерживаются для совместимости со старыми parser-проектами.

## Команды бота

- `/start` — подписать текущий чат на уведомления.
- `/stop` — отписать текущий чат.
- `/status` — показать количество источников, подписчиков и лидов.
- `/sources` — показать активные источники.
- `/keywords` — показать текущие ключевые и стоп-слова.
- `/test текст` — проверить, пройдет ли текст через фильтр.

## Запуск в фоне

Обычный локальный запуск:

```bash
python -m freelancer_bot
```

Простой запуск в фоне:

```bash
nohup python -m freelancer_bot > bot.log 2>&1 &
```

Для VPS лучше использовать `systemd`, Docker или process manager. Файлы `.env`, `sessions/` и `data/` должны оставаться приватными.

## Проверка

```bash
python -m unittest discover -s tests
python -m py_compile freelancer_bot/*.py
```

## Безопасность

Никогда не публикуйте:

- `.env`
- `sessions/`
- `*.session`
- `*.db`
- `data/`

Файлы Telegram-сессии могут дать доступ к вашему Telegram-аккаунту. Относитесь к ним как к паролям.

## Ответственное использование

Мониторьте только те источники, к которым у вашего Telegram-аккаунта есть доступ. Проект предназначен для личного поиска лидов и ручных ответов, а не для спама или автоматической массовой рассылки.

## Лицензия

MIT. Подробнее в [LICENSE](LICENSE).

