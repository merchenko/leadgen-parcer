# Security

Do not publish Telegram credentials or session files.

Private files include:

- `.env`
- `sessions/`
- `*.session`
- `*.db`
- `data/`

If you accidentally publish a bot token, revoke it in [@BotFather](https://t.me/BotFather).

If you accidentally publish a Telethon user session, log out that session from Telegram:

1. Open Telegram.
2. Go to Settings -> Devices.
3. Terminate the leaked session.
4. Delete the leaked session file.

This project is intended for personal monitoring of sources your Telegram account can access. Do not use it for spam or unauthorized scraping.

