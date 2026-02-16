"""
Main Matrix Bot Implementation
"""

import asyncio
import logging
import ssl
from typing import Optional

import aiohttp
import certifi

from nio import AsyncClient, AsyncClientConfig, RoomMessage, MatrixRoom, InviteEvent, ReactionEvent
from nio.responses import LoginResponse, SyncResponse

from config import Config
from database import Database
from lastfm_client import LastfmClient
from discogs_client import DiscogsClient
from commands import CommandHandler

logger = logging.getLogger(__name__)


class FMatrixBot:
    """Matrix bot for Last.fm stats and leaderboards."""

    def __init__(self, config_path=None):
        self.config = Config(config_path=config_path)
        self.client: Optional[AsyncClient] = None
        self.db: Optional[Database] = None
        self.lastfm = LastfmClient(
            self.config.lastfm_api_key,
            self.config.lastfm_api_secret
        )
        self.discogs = None
        if self.config.discogs_user_token:
            self.discogs = DiscogsClient(self.config.discogs_user_token)
        self.command_handler: Optional[CommandHandler] = None

    async def init_db(self):
        """Initialize the database."""
        self.db = Database(self.config.db_path)
        await self.db.init()
        logger.info("Database initialized")

    async def setup_client(self):
        """Set up the Matrix client."""
        client_config = AsyncClientConfig(
            request_timeout=120,
            max_timeouts=10,
        )
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        self.client = AsyncClient(
            self.config.matrix_homeserver,
            self.config.matrix_user_id,
            config=client_config,
            ssl=ssl_context,
        )

        logger.info(f"Matrix client initialized for {self.config.matrix_user_id}")

    async def check_homeserver(self):
        """Probe the homeserver to surface connectivity errors early."""
        url = f"{self.config.matrix_homeserver}/_matrix/client/versions"
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, ssl=ssl_context) as resp:
                    logger.info(
                        "Homeserver probe OK: %s (status %s)",
                        url,
                        resp.status,
                    )
        except Exception as e:
            logger.error("Homeserver probe failed: %s (%s)", url, e)

    async def login(self):
        """Log in to the Matrix server."""
        login_response = await self.client.login(self.config.matrix_password)

        if isinstance(login_response, LoginResponse):
            logger.info(f"Logged in successfully. Device ID: {login_response.device_id}")
        else:
            logger.error(f"Login failed: {login_response}")
            raise RuntimeError("Failed to login to Matrix server")

    async def join_configured_rooms(self):
        """Join pre-configured rooms."""
        if not self.config.auto_join_rooms:
            logger.warning("No rooms configured in AUTO_JOIN_ROOMS. Skipping auto-join.")
            return

        for room in self.config.auto_join_rooms:
            try:
                response = await self.client.join(room)
                if hasattr(response, 'room_id'):
                    logger.info(f"Successfully joined room: {room}")
                else:
                    logger.error(f"Failed to join room {room}: {response}")
            except Exception as e:
                logger.error(f"Failed to join room {room}: {e}")

    async def accept_pending_invites(self):
        """Accept all pending room invites."""
        for room_id in list(self.client.invited_rooms):
            try:
                response = await self.client.join(room_id)
                if hasattr(response, 'room_id'):
                    logger.info(f"Auto-accepted invite to room: {room_id}")
                else:
                    logger.error(f"Failed to accept invite to {room_id}: {response}")
            except Exception as e:
                logger.error(f"Failed to accept invite to {room_id}: {e}")

    async def invite_callback(self, room: MatrixRoom, event: InviteEvent):
        """Handle room invites - auto-join any room we're invited to."""
        room_id = room.room_id
        logger.info(f"Received invite to room: {room_id}")
        try:
            response = await self.client.join(room_id)
            if hasattr(response, 'room_id'):
                logger.info(f"Successfully joined room after invite: {room_id}")
            else:
                logger.error(f"Failed to join invited room {room_id}: {response}")
        except Exception as e:
            logger.error(f"Failed to join invited room {room_id}: {e}")

    async def message_callback(self, room: MatrixRoom, event: RoomMessage):
        """Handle incoming messages."""
        try:
            # Ignore messages from the bot itself
            if event.sender == self.config.matrix_user_id:
                return

            # Check if message starts with command prefix
            if not event.body.startswith(self.config.command_prefix):
                return

            # Handle command
            await self.command_handler.handle_command(
                room=room,
                sender=event.sender,
                message=event.body,
                client=self.client
            )

        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)

    async def reaction_callback(self, room: MatrixRoom, event: ReactionEvent):
        """Handle reaction events for pagination."""
        try:
            logger.info(f"Reaction callback triggered - Sender: {event.sender}")
            logger.info(f"Reacted to event: {event.reacts_to}, Key: {event.key}")

            # Ignore reactions from the bot itself
            if event.sender == self.config.matrix_user_id:
                logger.info("Ignoring reaction from bot itself")
                return

            # Handle the reaction
            await self.command_handler.handle_reaction(
                room=room,
                event=event,
                sender=event.sender,
                client=self.client
            )

        except Exception as e:
            logger.error(f"Error handling reaction: {e}", exc_info=True)

    async def run(self):
        """Run the bot."""
        await self.setup_client()
        await self.check_homeserver()
        self.command_handler = CommandHandler(
            self.db,
            self.lastfm,
            self.discogs,
            self.config
        )

        await self.login()

        # Join configured rooms
        await self.join_configured_rooms()

        # Accept any pending invites from before bot started
        await self.accept_pending_invites()

        # Start cache cleanup background task
        asyncio.create_task(self.cache_cleanup_loop())

        logger.info("Starting sync loop...")
        # Use custom sync loop to handle invites
        await self.sync_with_invite_handling()

    async def cache_cleanup_loop(self):
        """Periodically clean up old cache entries."""
        runs = 0
        while True:
            try:
                # Wait 1 hour between cleanups
                await asyncio.sleep(3600)
                logger.info("Running cache cleanup...")
                await self.db.clear_old_cache(max_age_hours=24)
                await self.db.clear_old_playcount_cache(max_age_hours=24)
                await self.db.clear_old_auth_tokens(max_age_hours=24)
                runs += 1
                if runs % 24 == 0:
                    await self.db.optimize()
                logger.info("Cache cleanup completed")
            except Exception as e:
                logger.error(f"Error during cache cleanup: {e}", exc_info=True)

    async def sync_with_invite_handling(self):
        """Sync loop that handles room invites automatically."""
        # First sync: establish sync token without processing events
        logger.info("Initial sync - establishing connection...")
        initial_sync = await self.client.sync(timeout=30000)

        # NOW set up event handlers after we have the sync token
        self.client.add_event_callback(
            self.message_callback,
            RoomMessage
        )

        # Add callback for room invites to auto-join
        self.client.add_event_callback(
            self.invite_callback,
            InviteEvent
        )

        # Add callback for reactions (pagination)
        self.client.add_event_callback(
            self.reaction_callback,
            ReactionEvent
        )

        logger.info("Bot ready - processing new messages only")
        while True:
            # Check for new invites before syncing
            await self.accept_pending_invites()

            # Sync with new messages only (10 second timeout for fast message polling)
            sync_response = await self.client.sync(timeout=10000)

            if not isinstance(sync_response, SyncResponse):
                logger.warning(f"Sync failed: {sync_response}")
                continue
