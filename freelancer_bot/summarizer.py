from __future__ import annotations

import os
import anthropic

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


async def summarize(text: str) -> str:
    client = _get_client()
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": (
                "Ты анализируешь пост с фриланс-биржи. Извлеки ключевую информацию строго в таком формате:\n\n"
                "📋 Задача: (одна строка — суть что нужно сделать)\n"
                "💰 Бюджет: (сумма или «не указан»)\n"
                "⏱ Срок: (срок или «не указан»)\n"
                "🔄 Тип: (разовая / постоянная / не указан)\n\n"
                "Только эти 4 строки, без лишних слов.\n\n"
                f"Пост:\n{text[:1500]}"
            ),
        }],
    )
    return response.content[0].text.strip()
