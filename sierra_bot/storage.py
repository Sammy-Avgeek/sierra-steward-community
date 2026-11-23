import asyncio
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Warning:
    interaction_id: int | str
    guild_id: int
    channel_id: int
    user_id: int
    moderator_id: int
    reason: str
    created_at: datetime


class WarningStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    interaction_id TEXT NOT NULL UNIQUE,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    moderator_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    async def record(self, warning: Warning) -> tuple[int, bool]:
        """Return the warning ID and whether this interaction created a new record."""
        return await asyncio.to_thread(self._record, warning)

    def _record(self, warning: Warning) -> tuple[int, bool]:
        # A separate connection per worker keeps the event loop and SQLite threads independent.
        # Store snowflakes as text to preserve the full unsigned 64-bit range.
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            inserted = db.execute(
                """
                INSERT INTO warnings
                    (interaction_id, guild_id, channel_id, user_id, moderator_id, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (interaction_id) DO NOTHING
                """,
                (
                    str(warning.interaction_id),
                    str(warning.guild_id),
                    str(warning.channel_id),
                    str(warning.user_id),
                    str(warning.moderator_id),
                    warning.reason,
                    warning.created_at.isoformat(timespec="seconds"),
                ),
            )
            if inserted.rowcount == 1:
                return int(inserted.lastrowid), True

            row = db.execute(
                "SELECT id FROM warnings WHERE interaction_id = ?",
                (str(warning.interaction_id),),
            ).fetchone()
            return int(row[0]), False

    def recent(self, guild_id: int, user_id: int | None = None) -> list[tuple]:
        """Return up to 100 newest warning records for the requested server or member."""
        if not self.path.is_file():
            return []
        query = """
            SELECT id, user_id, moderator_id, reason, created_at, channel_id
            FROM warnings WHERE guild_id = ?
        """
        parameters = [str(guild_id)]
        if user_id is not None:
            query += " AND user_id = ?"
            parameters.append(str(user_id))
        query += " ORDER BY id DESC LIMIT 100"
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            return db.execute(query, parameters).fetchall()
