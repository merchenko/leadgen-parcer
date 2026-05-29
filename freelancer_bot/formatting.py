from __future__ import annotations

import html
import re
from datetime import datetime

from .sources import Source
from .storage import LeadRecord


CONTACT_RE = re.compile(
    r"(?P<username>@[A-Za-z0-9_]{5,32})|(?P<email>[\w.+-]+@[\w-]+\.[\w.-]+)|(?P<url>https?://\S+)"
)


def extract_contacts(text: str) -> tuple[str, ...]:
    contacts: list[str] = []
    for match in CONTACT_RE.finditer(text):
        value = match.group(0).rstrip(".,;)")
        if value not in contacts:
            contacts.append(value)
    return tuple(contacts[:5])


def format_lead(source: Source, lead: LeadRecord, summary: str | None = None) -> str:
    contacts = extract_contacts(lead.text)
    contact_line = "  |  ".join(html.escape(c) for c in contacts) if contacts else "не найдены"

    date_text = lead.message_date
    try:
        date_text = datetime.fromisoformat(lead.message_date).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        pass

    link_line = f'<a href="{html.escape(lead.link)}">Открыть пост →</a>' if lead.link else ""

    if summary:
        summary_block = f"\n{html.escape(summary)}\n"
    else:
        truncated = re.sub(r"\s+", " ", lead.text).strip()[:300]
        summary_block = f"\n<i>{html.escape(truncated)}...</i>\n"

    return (
        f"🔔 <b>Новый лид</b> · {html.escape(source.title)}\n"
        f"📅 {date_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
        f"{summary_block}"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📬 <b>Контакты:</b> {contact_line}\n\n"
        f"🔗 {link_line}"
    )
