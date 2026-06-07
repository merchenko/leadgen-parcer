from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LeadRecord:
    source: str
    message_id: int
    link: str
    text: str
    score: int
    keywords: tuple[str, ...]
    message_date: str


@dataclass(frozen=True)
class SourceRow:
    handle: str
    title: str


@dataclass(frozen=True)
class KeywordRow:
    word: str
    weight: int
    is_stop: bool


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        # In-memory caches — invalidated on every write
        self._kw_cache: tuple[dict[str, int], list[str]] | None = None
        self._sub_cache: list[int] | None = None
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                link TEXT NOT NULL,
                text TEXT NOT NULL,
                score INTEGER NOT NULL,
                keywords_json TEXT NOT NULL,
                message_date TEXT NOT NULL,
                notified_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(source, message_id)
            );

            CREATE TABLE IF NOT EXISTS sources (
                handle TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS keywords (
                word TEXT PRIMARY KEY,
                weight INTEGER NOT NULL DEFAULT 2,
                is_stop INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── subscribers ──────────────────────────────────────────────────────────

    def add_subscriber(self, chat_id: int) -> None:
        self._conn.execute(
            "INSERT INTO subscribers(chat_id, created_at) VALUES(?, ?) ON CONFLICT(chat_id) DO NOTHING",
            (chat_id, utc_now()),
        )
        self._conn.commit()
        self._sub_cache = None

    def remove_subscriber(self, chat_id: int) -> None:
        self._conn.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))
        self._conn.commit()
        self._sub_cache = None

    def subscribers(self) -> list[int]:
        if self._sub_cache is None:
            rows = self._conn.execute("SELECT chat_id FROM subscribers ORDER BY created_at").fetchall()
            self._sub_cache = [int(row["chat_id"]) for row in rows]
        return self._sub_cache

    # ── leads ─────────────────────────────────────────────────────────────────

    def record_or_should_retry(self, lead: LeadRecord) -> bool:
        existing = self._conn.execute(
            "SELECT notified_at FROM leads WHERE source = ? AND message_id = ?",
            (lead.source, lead.message_id),
        ).fetchone()
        if existing:
            return existing["notified_at"] is None

        self._conn.execute(
            """
            INSERT INTO leads(source, message_id, link, text, score, keywords_json,
                              message_date, notified_at, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                lead.source, lead.message_id, lead.link, lead.text, lead.score,
                json.dumps(list(lead.keywords), ensure_ascii=False),
                lead.message_date, utc_now(),
            ),
        )
        self._conn.commit()
        return True

    def mark_notified(self, source: str, message_id: int) -> None:
        self._conn.execute(
            "UPDATE leads SET notified_at = ? WHERE source = ? AND message_id = ?",
            (utc_now(), source, message_id),
        )
        self._conn.commit()

    def stats(self) -> dict[str, int]:
        row = self._conn.execute(
            "SELECT COUNT(*) AS leads,"
            " SUM(CASE WHEN notified_at IS NULL THEN 1 ELSE 0 END) AS pending"
            " FROM leads"
        ).fetchone()
        sub_count = self._conn.execute("SELECT COUNT(*) AS c FROM subscribers").fetchone()["c"]
        return {
            "leads": int(row["leads"] or 0),
            "pending": int(row["pending"] or 0),
            "subscribers": int(sub_count),
        }

    def add_initial_subscribers(self, chat_ids: Iterable[int]) -> None:
        for chat_id in chat_ids:
            self.add_subscriber(chat_id)

    # ── sources ───────────────────────────────────────────────────────────────

    def seed_sources(self, defaults: list[tuple[str, str]]) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO sources(handle, title, enabled) VALUES(?, ?, 1)",
            defaults,
        )
        self._conn.commit()

    def get_enabled_sources(self) -> list[SourceRow]:
        rows = self._conn.execute(
            "SELECT handle, title FROM sources WHERE enabled = 1 ORDER BY rowid"
        ).fetchall()
        return [SourceRow(r["handle"], r["title"]) for r in rows]

    def get_sources(self) -> list[tuple[SourceRow, bool]]:
        rows = self._conn.execute(
            "SELECT handle, title, enabled FROM sources ORDER BY rowid"
        ).fetchall()
        return [(SourceRow(r["handle"], r["title"]), bool(r["enabled"])) for r in rows]

    def add_source(self, handle: str, title: str) -> bool:
        """Returns True if newly added, False if already existed (re-enabled)."""
        try:
            self._conn.execute(
                "INSERT INTO sources(handle, title, enabled) VALUES(?, ?, 1)", (handle, title)
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            self._conn.execute("UPDATE sources SET enabled = 1 WHERE handle = ?", (handle,))
            self._conn.commit()
            return False

    def remove_source(self, handle: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM sources WHERE handle = ?", (handle,)).fetchone()
        if not row:
            return False
        self._conn.execute("UPDATE sources SET enabled = 0 WHERE handle = ?", (handle,))
        self._conn.commit()
        return True

    # ── keywords ──────────────────────────────────────────────────────────────

    def seed_keywords(self, keywords: dict[str, int], stop_words: list[str]) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO keywords(word, weight, is_stop) VALUES(?, ?, 0)",
            list(keywords.items()),
        )
        self._conn.executemany(
            "INSERT OR IGNORE INTO keywords(word, weight, is_stop) VALUES(?, 0, 1)",
            [(w,) for w in stop_words],
        )
        self._conn.commit()
        self._kw_cache = None

    def _load_keywords(self) -> tuple[dict[str, int], list[str]]:
        rows = self._conn.execute("SELECT word, weight, is_stop FROM keywords").fetchall()
        kw = {r["word"]: r["weight"] for r in rows if not r["is_stop"]}
        sw = [r["word"] for r in rows if r["is_stop"]]
        return kw, sw

    def get_active_keywords(self) -> dict[str, int]:
        if self._kw_cache is None:
            self._kw_cache = self._load_keywords()
        return self._kw_cache[0]

    def get_stop_words(self) -> list[str]:
        if self._kw_cache is None:
            self._kw_cache = self._load_keywords()
        return self._kw_cache[1]

    def get_keywords(self) -> list[KeywordRow]:
        rows = self._conn.execute(
            "SELECT word, weight, is_stop FROM keywords ORDER BY is_stop, word"
        ).fetchall()
        return [KeywordRow(r["word"], r["weight"], bool(r["is_stop"])) for r in rows]

    def add_keyword(self, word: str, weight: int, is_stop: bool) -> bool:
        try:
            self._conn.execute(
                "INSERT INTO keywords(word, weight, is_stop) VALUES(?, ?, ?)",
                (word, weight, int(is_stop)),
            )
            self._conn.commit()
            self._kw_cache = None
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_keyword(self, word: str) -> bool:
        self._conn.execute("DELETE FROM keywords WHERE word = ?", (word,))
        changed = self._conn.execute("SELECT changes() AS c").fetchone()["c"]
        self._conn.commit()
        if changed:
            self._kw_cache = None
        return bool(changed)
