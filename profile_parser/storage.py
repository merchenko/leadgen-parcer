from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ProfileRecord:
    user_id: int
    username: str | None
    full_name: str
    about: str
    profile_url: str
    source_chat: str


class ProfileStorage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Fix #3: check_same_thread=False для asyncio
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")  # лучше для конкурентного доступа
        self._init_schema()
        self._migrate()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS profiles (
                user_id             INTEGER PRIMARY KEY,
                username            TEXT,
                full_name           TEXT NOT NULL,
                about               TEXT NOT NULL DEFAULT '',
                profile_url         TEXT NOT NULL,
                source_chat         TEXT NOT NULL,
                category            TEXT,
                notified            INTEGER NOT NULL DEFAULT 0,
                stories_viewed_at   TEXT,
                stories_count       INTEGER NOT NULL DEFAULT 0,
                created_at          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chats (
                handle      TEXT PRIMARY KEY,
                enabled     INTEGER NOT NULL DEFAULT 1,
                label       TEXT,
                category    TEXT
            );

            CREATE TABLE IF NOT EXISTS keywords (
                word        TEXT PRIMARY KEY,
                is_stop     INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS reactions_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     TEXT NOT NULL,
                message_id  INTEGER NOT NULL,
                account     TEXT NOT NULL,
                reacted_at  TEXT NOT NULL,
                UNIQUE(chat_id, message_id, account)
            );

            CREATE TABLE IF NOT EXISTS accounts (
                phone           TEXT PRIMARY KEY,
                session_path    TEXT NOT NULL,
                label           TEXT,
                enabled         INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key     TEXT PRIMARY KEY,
                value   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tenants (
                tg_id        INTEGER PRIMARY KEY,
                username     TEXT,
                api_id       INTEGER,
                api_hash     TEXT,
                phone        TEXT,
                session_path TEXT,
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_profiles_notified
                ON profiles(notified);
            CREATE INDEX IF NOT EXISTS idx_profiles_stories
                ON profiles(stories_viewed_at);
            CREATE INDEX IF NOT EXISTS idx_reactions_account
                ON reactions_log(account, reacted_at);
        """)
        self._conn.commit()

    def _migrate(self) -> None:
        """Добавляет колонки которых нет в старых БД."""
        existing = {r[1] for r in self._conn.execute("PRAGMA table_info(profiles)").fetchall()}
        migrations = {
            "category":           "ALTER TABLE profiles ADD COLUMN category TEXT",
            "stories_viewed_at":  "ALTER TABLE profiles ADD COLUMN stories_viewed_at TEXT",
            "stories_count":      "ALTER TABLE profiles ADD COLUMN stories_count INTEGER NOT NULL DEFAULT 0",
        }
        for col, sql in migrations.items():
            if col not in existing:
                self._conn.execute(sql)
        self._conn.commit()

        existing_chats = {r[1] for r in self._conn.execute("PRAGMA table_info(chats)").fetchall()}
        chat_migrations = {
            "label":    "ALTER TABLE chats ADD COLUMN label TEXT",
            "category": "ALTER TABLE chats ADD COLUMN category TEXT",
        }
        for col, sql in chat_migrations.items():
            if col not in existing_chats:
                self._conn.execute(sql)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── profiles ─────────────────────────────────────────────────────────────

    def is_seen(self, user_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row is not None

    def get_chat_category(self, chat_handle: str) -> str | None:
        row = self._conn.execute(
            "SELECT category FROM chats WHERE handle=?", (chat_handle,)
        ).fetchone()
        return row["category"] if row else None

    def save_profile(self, p: ProfileRecord) -> bool:
        """Returns True if newly saved, False if already exists."""
        category = self.get_chat_category(p.source_chat)
        try:
            self._conn.execute(
                """INSERT INTO profiles(user_id, username, full_name, about,
                   profile_url, source_chat, category, notified, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                (p.user_id, p.username, p.full_name, p.about,
                 p.profile_url, p.source_chat, category, utc_now()),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def mark_notified(self, user_id: int) -> None:
        self._conn.execute(
            "UPDATE profiles SET notified = 1 WHERE user_id = ?", (user_id,)
        )
        self._conn.commit()

    def get_pending_profiles(self) -> list[ProfileRecord]:
        rows = self._conn.execute(
            "SELECT user_id, username, full_name, about, profile_url, source_chat "
            "FROM profiles WHERE notified = 0 ORDER BY created_at"
        ).fetchall()
        return [
            ProfileRecord(
                user_id=r["user_id"],
                username=r["username"],
                full_name=r["full_name"],
                about=r["about"],
                profile_url=r["profile_url"],
                source_chat=r["source_chat"],
            )
            for r in rows
        ]

    # ── stories ───────────────────────────────────────────────────────────────

    def get_profiles_for_stories(self, limit: int = 150) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT user_id, username, profile_url
            FROM profiles
            WHERE username IS NOT NULL
              AND (
                stories_viewed_at IS NULL
                OR date(stories_viewed_at) < date('now')
              )
            ORDER BY stories_viewed_at ASC NULLS FIRST
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {"user_id": r["user_id"], "username": r["username"], "profile_url": r["profile_url"]}
            for r in rows
        ]

    def mark_stories_viewed(self, user_id: int) -> None:
        self._conn.execute(
            """UPDATE profiles
               SET stories_viewed_at = ?,
                   stories_count = stories_count + 1
               WHERE user_id = ?""",
            (utc_now(), user_id),
        )
        self._conn.commit()

    # ── reactions ─────────────────────────────────────────────────────────────

    def is_reacted(self, chat_id: str, message_id: int, account: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM reactions_log WHERE chat_id=? AND message_id=? AND account=?",
            (chat_id, message_id, account),
        ).fetchone()
        return row is not None

    def log_reaction(self, chat_id: str, message_id: int, account: str) -> None:
        try:
            self._conn.execute(
                "INSERT INTO reactions_log(chat_id, message_id, account, reacted_at) VALUES(?,?,?,?)",
                (chat_id, message_id, account, utc_now()),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            pass

    # ── accounts ─────────────────────────────────────────────────────────────

    def add_account(self, phone: str, session_path: str, label: str = "") -> bool:
        try:
            self._conn.execute(
                "INSERT INTO accounts(phone, session_path, label, enabled, created_at) VALUES(?,?,?,1,?)",
                (phone, session_path, label, utc_now()),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_enabled_accounts(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT phone, session_path, label FROM accounts WHERE enabled = 1"
        ).fetchall()
        return [{"phone": r["phone"], "session_path": r["session_path"], "label": r["label"]} for r in rows]

    def remove_account(self, phone: str) -> bool:
        self._conn.execute("UPDATE accounts SET enabled = 0 WHERE phone = ?", (phone,))
        changed = self._conn.execute("SELECT changes() AS c").fetchone()["c"]
        self._conn.commit()
        return bool(changed)

    # ── chats ─────────────────────────────────────────────────────────────────

    def get_enabled_chats(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT handle FROM chats WHERE enabled = 1"
        ).fetchall()
        return [r["handle"] for r in rows]

    def add_chat(self, handle: str) -> bool:
        try:
            self._conn.execute(
                "INSERT INTO chats(handle, enabled) VALUES(?, 1)", (handle,)
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            self._conn.execute("UPDATE chats SET enabled = 1 WHERE handle = ?", (handle,))
            self._conn.commit()
            return False

    def remove_chat(self, handle: str) -> bool:
        self._conn.execute("UPDATE chats SET enabled = 0 WHERE handle = ?", (handle,))
        changed = self._conn.execute("SELECT changes() AS c").fetchone()["c"]
        self._conn.commit()
        return bool(changed)

    # ── keywords ─────────────────────────────────────────────────────────────

    def get_keywords(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT word FROM keywords WHERE is_stop = 0"
        ).fetchall()
        return [r["word"] for r in rows]

    def get_stop_words(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT word FROM keywords WHERE is_stop = 1"
        ).fetchall()
        return [r["word"] for r in rows]

    def seed_keywords(self, keywords: list[str], stop_words: list[str] | None = None) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO keywords(word, is_stop) VALUES(?, 0)",
            [(w,) for w in keywords],
        )
        if stop_words:
            self._conn.executemany(
                "INSERT OR IGNORE INTO keywords(word, is_stop) VALUES(?, 1)",
                [(w,) for w in stop_words],
            )
        self._conn.commit()

    def add_keyword(self, word: str, is_stop: bool = False) -> bool:
        try:
            self._conn.execute(
                "INSERT INTO keywords(word, is_stop) VALUES(?, ?)",
                (word, int(is_stop)),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_keyword(self, word: str) -> bool:
        self._conn.execute("DELETE FROM keywords WHERE word = ?", (word,))
        changed = self._conn.execute("SELECT changes() AS c").fetchone()["c"]
        self._conn.commit()
        return bool(changed)

    # ── tenants ──────────────────────────────────────────────────────────────

    def get_tenant(self, tg_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM tenants WHERE tg_id=?", (tg_id,)
        ).fetchone()
        return dict(row) if row else None

    def upsert_tenant(self, tg_id: int, **kwargs) -> None:
        existing = self.get_tenant(tg_id)
        if not existing:
            kwargs["tg_id"] = tg_id
            kwargs.setdefault("created_at", utc_now())
            kwargs.setdefault("status", "pending")
            cols = ", ".join(kwargs.keys())
            vals = ", ".join("?" * len(kwargs))
            self._conn.execute(
                f"INSERT INTO tenants({cols}) VALUES({vals})",
                list(kwargs.values()),
            )
        else:
            sets = ", ".join(f"{k}=?" for k in kwargs)
            self._conn.execute(
                f"UPDATE tenants SET {sets} WHERE tg_id=?",
                [*kwargs.values(), tg_id],
            )
        self._conn.commit()

    def get_active_tenants(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM tenants WHERE status='active'"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── settings ─────────────────────────────────────────────────────────────

    def get_setting(self, key: str, default: str = "1") -> str:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    def toggle_setting(self, key: str) -> bool:
        """Переключает 0/1, возвращает новое значение."""
        current = self.get_setting(key, "1")
        new_val = "0" if current == "1" else "1"
        self.set_setting(key, new_val)
        return new_val == "1"

    # ── stats ─────────────────────────────────────────────────────────────────

    def full_stats(self) -> dict:
        rows = self._conn.execute(
            "SELECT category, COUNT(*) as cnt FROM profiles GROUP BY category"
        ).fetchall()
        by_category = {(r["category"] or "общая"): r["cnt"] for r in rows}

        stories_viewed = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM profiles WHERE stories_viewed_at IS NOT NULL"
        ).fetchone()["cnt"]

        stories_today = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM profiles WHERE date(stories_viewed_at)=date('now')"
        ).fetchone()["cnt"]

        stories_reactions = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM reactions_log WHERE chat_id LIKE 'stories:%'"
        ).fetchone()["cnt"]

        reactions_total = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM reactions_log WHERE chat_id NOT LIKE 'stories:%'"
        ).fetchone()["cnt"]

        reactions_today = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM reactions_log "
            "WHERE chat_id NOT LIKE 'stories:%' AND date(reacted_at)=date('now')"
        ).fetchone()["cnt"]

        acc_rows = self._conn.execute(
            "SELECT account, COUNT(*) as cnt FROM reactions_log "
            "WHERE chat_id NOT LIKE 'stories:%' GROUP BY account"
        ).fetchall()
        by_account = {r["account"]: r["cnt"] for r in acc_rows}

        return {
            "total_profiles": sum(by_category.values()),
            "by_category": by_category,
            "stories_viewed": stories_viewed,
            "stories_today": stories_today,
            "stories_reactions": stories_reactions,
            "reactions_total": reactions_total,
            "reactions_today": reactions_today,
            "by_account": by_account,
        }
