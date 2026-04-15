"""SQLite database for tracking seen posts and notification history."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

from social_monitor.config import DEFAULT_CONFIG_DIR
from social_monitor.models import Post, ScoredPost

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = DEFAULT_CONFIG_DIR / "social_monitor.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_posts (
    source TEXT NOT NULL,
    post_id TEXT NOT NULL,
    title TEXT,
    body_preview TEXT,
    url TEXT,
    author TEXT,
    timestamp DATETIME,
    score REAL,
    explanation TEXT,
    notified INTEGER DEFAULT 0,
    source_name TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, post_id)
);

CREATE INDEX IF NOT EXISTS idx_seen_posts_created ON seen_posts(created_at);
CREATE INDEX IF NOT EXISTS idx_seen_posts_score ON seen_posts(score);
"""

MIGRATION = """
-- Add source_name column if it doesn't exist (safe to run multiple times)
ALTER TABLE seen_posts ADD COLUMN source_name TEXT DEFAULT '';
"""


class Database:
    """Async SQLite wrapper for post tracking."""

    def __init__(self, path: Path | None = None):
        self._path = path or DEFAULT_DB_PATH.expanduser()
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        # Migrate: add source_name column to existing databases
        try:
            await self._db.execute("ALTER TABLE seen_posts ADD COLUMN source_name TEXT DEFAULT ''")
        except Exception:
            pass  # Column already exists
        await self._db.commit()
        logger.info("Database opened at %s", self._path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def is_seen(self, source: str, post_id: str) -> bool:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT 1 FROM seen_posts WHERE source = ? AND post_id = ?",
            (source, post_id),
        )
        return await cursor.fetchone() is not None

    async def mark_seen(self, post: Post) -> None:
        assert self._db is not None
        await self._db.execute(
            """INSERT OR IGNORE INTO seen_posts
               (source, post_id, title, body_preview, url, author, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                post.source,
                post.post_id,
                post.title,
                post.body if post.body else "",
                post.url,
                post.author,
                post.timestamp.isoformat() if post.timestamp else None,
            ),
        )
        await self._db.commit()

    async def save_scored(self, scored: ScoredPost, notified: bool = False) -> None:
        assert self._db is not None
        source_name = scored.post.metadata.get("source_name", "")
        await self._db.execute(
            """INSERT INTO seen_posts
               (source, post_id, title, body_preview, url, author, timestamp,
                score, explanation, notified, source_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source, post_id) DO UPDATE SET
                   score = excluded.score,
                   explanation = excluded.explanation,
                   notified = excluded.notified,
                   source_name = excluded.source_name,
                   body_preview = excluded.body_preview""",
            (
                scored.post.source,
                scored.post.post_id,
                scored.post.title,
                scored.post.body if scored.post.body else "",
                scored.post.url,
                scored.post.author,
                scored.post.timestamp.isoformat() if scored.post.timestamp else None,
                scored.score,
                scored.explanation,
                int(notified),
                source_name,
            ),
        )
        await self._db.commit()

    async def get_recent_matches(
        self, limit: int = 50, min_score: float = 0.0
    ) -> list[dict]:
        """Return recent scored posts above the minimum score."""
        assert self._db is not None
        cursor = await self._db.execute(
            """SELECT source, post_id, title, body_preview, url, author, timestamp,
                      score, explanation, notified, source_name, created_at
               FROM seen_posts
               WHERE score IS NOT NULL AND score >= ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (min_score, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def prune_old(self, days: int = 30) -> int:
        """Delete posts older than the specified number of days. Returns count deleted."""
        assert self._db is not None
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cursor = await self._db.execute(
            "DELETE FROM seen_posts WHERE created_at < ?", (cutoff,)
        )
        await self._db.commit()
        return cursor.rowcount

    async def clear_all(self) -> int:
        """Delete ALL seen posts. Returns count deleted."""
        assert self._db is not None
        cursor = await self._db.execute("DELETE FROM seen_posts")
        await self._db.commit()
        logger.info("Cleared %d posts from database", cursor.rowcount)
        return cursor.rowcount
