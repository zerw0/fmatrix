"""
Database management for user Last.fm mappings and cached data
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
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
                lastfm_session_key TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS discogs_mappings (
                matrix_user_id TEXT PRIMARY KEY,
                discogs_username TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS stats_cache (
                lastfm_username TEXT PRIMARY KEY,
                stats_json TEXT NOT NULL,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS auth_tokens (
                matrix_user_id TEXT PRIMARY KEY,
                auth_token TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS lastfm_cache (
                cache_key TEXT PRIMARY KEY,
                response_json TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS room_settings (
                room_id TEXT PRIMARY KEY,
                setting_name TEXT NOT NULL,
                setting_value TEXT,
                UNIQUE(room_id, setting_name)
            );

            CREATE INDEX IF NOT EXISTS idx_user_mappings_lastfm
                ON user_mappings(lastfm_username);

            CREATE INDEX IF NOT EXISTS idx_discogs_mappings_username
                ON discogs_mappings(discogs_username);

            CREATE INDEX IF NOT EXISTS idx_lastfm_cache_expires
                ON lastfm_cache(expires_at);
        """)

        await self.db.commit()
        logger.info(f"Database initialized at {self.db_path}")

        # Run migrations
        await self._migrate_database()

    async def _migrate_database(self):
        """Run database migrations."""
        try:
            # Check if lastfm_session_key column exists in user_mappings
            cursor = await self.db.execute(
                "PRAGMA table_info(user_mappings)"
            )
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            if 'lastfm_session_key' not in column_names:
                logger.info("Adding lastfm_session_key column to user_mappings table")
                await self.db.execute(
                    "ALTER TABLE user_mappings ADD COLUMN lastfm_session_key TEXT"
                )
                await self.db.commit()
                logger.info("Migration completed: lastfm_session_key column added")

            # Ensure lastfm_cache table exists for older databases
            cursor = await self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='lastfm_cache'"
            )
            if not await cursor.fetchone():
                logger.info("Creating lastfm_cache table")
                await self.db.executescript("""
                    CREATE TABLE IF NOT EXISTS lastfm_cache (
                        cache_key TEXT PRIMARY KEY,
                        response_json TEXT NOT NULL,
                        expires_at TIMESTAMP NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_lastfm_cache_expires
                        ON lastfm_cache(expires_at);
                """)
                await self.db.commit()
        except Exception as e:
            logger.error(f"Error running migrations: {e}", exc_info=True)

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

    async def set_lastfm_session_key(self, matrix_user_id: str, session_key: str) -> bool:
        """Store Last.fm session key for a user."""
        try:
            logger.info(f"Setting session key for user {matrix_user_id}")

            # First check if user exists
            cursor = await self.db.execute(
                "SELECT matrix_user_id FROM user_mappings WHERE matrix_user_id = ?",
                (matrix_user_id,)
            )
            row = await cursor.fetchone()
            if not row:
                logger.error(f"User {matrix_user_id} not found in user_mappings")
                return False

            logger.info(f"User {matrix_user_id} found, updating session key")

            await self.db.execute(
                """
                UPDATE user_mappings
                SET lastfm_session_key = ?, updated_at = CURRENT_TIMESTAMP
                WHERE matrix_user_id = ?
                """,
                (session_key, matrix_user_id)
            )
            await self.db.commit()
            logger.info(f"Session key set successfully for {matrix_user_id}")
            return True
        except Exception as e:
            logger.error(f"Error setting session key for {matrix_user_id}: {e}", exc_info=True)
            return False

    async def get_lastfm_session_key(self, matrix_user_id: str) -> str:
        """Get the Last.fm session key for a Matrix user."""
        cursor = await self.db.execute(
            "SELECT lastfm_session_key FROM user_mappings WHERE matrix_user_id = ?",
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

    async def get_lastfm_cache(self, cache_key: str) -> Optional[str]:
        """Get cached Last.fm response if not expired."""
        cursor = await self.db.execute(
            "SELECT response_json, expires_at FROM lastfm_cache WHERE cache_key = ?",
            (cache_key,)
        )
        row = await cursor.fetchone()
        if not row:
            return None

        response_json, expires_at_raw = row
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            await self.db.execute(
                "DELETE FROM lastfm_cache WHERE cache_key = ?",
                (cache_key,)
            )
            await self.db.commit()
            return None

        if datetime.now(timezone.utc) >= expires_at:
            await self.db.execute(
                "DELETE FROM lastfm_cache WHERE cache_key = ?",
                (cache_key,)
            )
            await self.db.commit()
            return None

        return response_json

    async def set_lastfm_cache(self, cache_key: str, response_json: str, ttl_seconds: int):
        """Cache a Last.fm response with TTL in seconds."""
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        await self.db.execute(
            """
            INSERT OR REPLACE INTO lastfm_cache
            (cache_key, response_json, expires_at)
            VALUES (?, ?, ?)
            """,
            (cache_key, response_json, expires_at.isoformat())
        )
        await self.db.commit()
    async def store_auth_token(self, matrix_user_id: str, auth_token: str) -> bool:
        """Store a pending auth token for a user."""
        try:
            await self.db.execute(
                """
                INSERT OR REPLACE INTO auth_tokens
                (matrix_user_id, auth_token, created_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (matrix_user_id, auth_token)
            )
            await self.db.commit()
            return True
        except Exception as e:
            logger.error(f"Error storing auth token: {e}")
            return False

    async def get_auth_token(self, matrix_user_id: str) -> Optional[str]:
        """Get the pending auth token for a user."""
        cursor = await self.db.execute(
            """
            SELECT auth_token FROM auth_tokens
            WHERE matrix_user_id = ?
            """,
            (matrix_user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def delete_auth_token(self, matrix_user_id: str) -> bool:
        """Delete the auth token for a user."""
        try:
            await self.db.execute(
                "DELETE FROM auth_tokens WHERE matrix_user_id = ?",
                (matrix_user_id,)
            )
            await self.db.commit()
            return True
        except Exception as e:
            logger.error(f"Error deleting auth token: {e}")
            return False

    async def clear_old_auth_tokens(self, max_age_hours: int = 24):
        """Clear auth tokens older than max_age_hours."""
        await self.db.execute(
            """
            DELETE FROM auth_tokens
            WHERE created_at < datetime('now', '-' || ? || ' hours')
            """,
            (max_age_hours,)
        )
        await self.db.commit()

    # Discogs-related methods
    async def link_discogs_user(self, matrix_user_id: str, discogs_username: str) -> bool:
        """Link a Matrix user to a Discogs account."""
        try:
            await self.db.execute(
                """
                INSERT OR REPLACE INTO discogs_mappings
                (matrix_user_id, discogs_username, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (matrix_user_id, discogs_username)
            )
            await self.db.commit()
            return True
        except Exception as e:
            logger.error(f"Error linking Discogs user: {e}")
            return False

    async def get_discogs_username(self, matrix_user_id: str) -> Optional[str]:
        """Get the Discogs username for a Matrix user."""
        cursor = await self.db.execute(
            "SELECT discogs_username FROM discogs_mappings WHERE matrix_user_id = ?",
            (matrix_user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def unlink_discogs_user(self, matrix_user_id: str) -> bool:
        """Unlink a Matrix user from their Discogs account."""
        try:
            await self.db.execute(
                "DELETE FROM discogs_mappings WHERE matrix_user_id = ?",
                (matrix_user_id,)
            )
            await self.db.commit()
            return True
        except Exception as e:
            logger.error(f"Error unlinking Discogs user: {e}")
            return False
