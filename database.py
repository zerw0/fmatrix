"""
Database management for user Last.fm mappings and cached data
"""

import logging
from datetime import datetime, timedelta
import aiosqlite

logger = logging.getLogger(__name__)


class Database:
    """SQLite database for storing user mappings and stats."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db: aiosqlite.Connection = None

    async def init(self):
        """Initialize the database with required tables."""
        self.db = await aiosqlite.connect(self.db_path)

        # Create tables
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS user_mappings (
                matrix_user_id TEXT PRIMARY KEY,
                lastfm_username TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS stats_cache (
                lastfm_username TEXT PRIMARY KEY,
                stats_json TEXT NOT NULL,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS room_settings (
                room_id TEXT PRIMARY KEY,
                setting_name TEXT NOT NULL,
                setting_value TEXT,
                UNIQUE(room_id, setting_name)
            );

            CREATE INDEX IF NOT EXISTS idx_user_mappings_lastfm
                ON user_mappings(lastfm_username);
        """)

        await self.db.commit()
        logger.info(f"Database initialized at {self.db_path}")

    async def close(self):
        """Close the database connection."""
        if self.db:
            await self.db.close()

    async def link_user(self, matrix_user_id: str, lastfm_username: str) -> bool:
        """Link a Matrix user to a Last.fm account."""
        try:
            await self.db.execute(
                """
                INSERT OR REPLACE INTO user_mappings
                (matrix_user_id, lastfm_username, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (matrix_user_id, lastfm_username)
            )
            await self.db.commit()
            return True
        except Exception as e:
            logger.error(f"Error linking user: {e}")
            return False

    async def get_lastfm_username(self, matrix_user_id: str) -> str:
        """Get the Last.fm username for a Matrix user."""
        cursor = await self.db.execute(
            "SELECT lastfm_username FROM user_mappings WHERE matrix_user_id = ?",
            (matrix_user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def get_all_users_in_room(self, room_id: str, matrix_users: list) -> dict:
        """Get all Last.fm usernames for Matrix users in a room."""
        placeholders = ','.join(['?' for _ in matrix_users])
        query = f"SELECT matrix_user_id, lastfm_username FROM user_mappings WHERE matrix_user_id IN ({placeholders})"

        cursor = await self.db.execute(query, matrix_users)
        rows = await cursor.fetchall()

        return {row[0]: row[1] for row in rows}

    async def cache_stats(self, lastfm_username: str, stats_json: str):
        """Cache Last.fm stats for a user."""
        await self.db.execute(
            """
            INSERT OR REPLACE INTO stats_cache
            (lastfm_username, stats_json, cached_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (lastfm_username, stats_json)
        )
        await self.db.commit()

    async def get_cached_stats(self, lastfm_username: str, max_age_hours: int = 1) -> str:
        """Get cached stats if they're fresh enough."""
        cursor = await self.db.execute(
            """
            SELECT stats_json, cached_at FROM stats_cache
            WHERE lastfm_username = ?
            """,
            (lastfm_username,)
        )
        row = await cursor.fetchone()

        if not row:
            return None

        cached_at = datetime.fromisoformat(row[1])
        if datetime.now() - cached_at > timedelta(hours=max_age_hours):
            return None

        return row[0]

    async def clear_old_cache(self, max_age_hours: int = 24):
        """Clear stats cache older than max_age_hours."""
        await self.db.execute(
            """
            DELETE FROM stats_cache
            WHERE cached_at < datetime('now', '-' || ? || ' hours')
            """,
            (max_age_hours,)
        )
        await self.db.commit()
