"""
Command handler for bot commands
"""

import logging
import json
import re
import aiohttp
from io import BytesIO
from typing import Optional, Dict, Callable, Any
from nio import AsyncClient, RoomMessage, MatrixRoom
from nio.responses import UploadResponse, UploadError
from PIL import Image, ImageDraw, ImageFont

from database import Database
from lastfm_client import LastfmClient
from config import Config

logger = logging.getLogger(__name__)


class PaginationManager:
    """Manages paginated messages with reaction-based navigation."""

    def __init__(self):
        # Store pagination state: event_id -> {room_id, user_id, current_page, total_pages, callback, reaction_event_ids}
        self.paginations: Dict[str, Dict[str, Any]] = {}

    def register(self, event_id: str, room_id: str, user_id: str, current_page: int,
                 total_pages: int, callback: Callable):
        """Register a paginated message."""
        self.paginations[event_id] = {
            'room_id': room_id,
            'user_id': user_id,
            'current_page': current_page,
            'total_pages': total_pages,
            'callback': callback,
            'reaction_event_ids': []  # Store reaction event IDs for later removal
        }

    def get(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Get pagination state for an event."""
        return self.paginations.get(event_id)

    def update_page(self, event_id: str, new_page: int):
        """Update the current page for a pagination."""
        if event_id in self.paginations:
            self.paginations[event_id]['current_page'] = new_page

    def add_reaction_event_id(self, event_id: str, reaction_event_id: str):
        """Store a reaction event ID for later removal."""
        if event_id in self.paginations:
            self.paginations[event_id]['reaction_event_ids'].append(reaction_event_id)

    def get_reaction_event_ids(self, event_id: str) -> list:
        """Get all reaction event IDs for a message."""
        if event_id in self.paginations:
            return self.paginations[event_id].get('reaction_event_ids', [])
        return []

    def clear_reaction_event_ids(self, event_id: str):
        """Clear stored reaction event IDs."""
        if event_id in self.paginations:
            self.paginations[event_id]['reaction_event_ids'] = []

    def remove(self, event_id: str):
        """Remove a pagination."""
        self.paginations.pop(event_id, None)

    def cleanup_old(self, max_age_seconds: int = 3600):
        """Remove old paginations (if needed in future)."""
        # For now, we'll keep them until explicitly removed
        pass


class CommandHandler:
    """Handles commands from Matrix messages."""

    # Command abbreviations
    COMMAND_ALIASES = {
        'fm': 'lastfm',
        'lastfm': 'lastfm',
        'ta': 'topalbums',
        'tb': 'topalbums',
        'tt': 'toptracks',
        'tar': 'topartists',
        'wk': 'whoknows',
        'whoknows': 'whoknows',
        'wkt': 'whoknowstrack',
        'whoknowstrack': 'whoknowstrack',
        'wka': 'whoknowsalbum',
        'whoknowsalbum': 'whoknowsalbum',
        'c': 'chart',
        'chart': 'chart',
        'lb': 'leaderboard',
        'r': 'recent',
        's': 'stats',
        'l': 'link',
        '?': 'help',
        'discogs': 'discogs',
        'dg': 'discogs',
        'dgc': 'dgcollection',
        'dgw': 'dgwantlist',
    }

    # Period abbreviations
    PERIOD_ALIASES = {
        '7d': '7days',
        '7day': '7days',
        '1m': '1month',
        '1month': '1month',
        '3m': '3month',
        '3month': '3month',
        '6m': '6month',
        '6month': '6month',
        '12m': '12month',
        '1y': 'overall',
        'y': 'overall',
        'all': 'overall',
    }

    # Valid periods for Last.fm API
    VALID_PERIODS = ['overall', '12month', '6month', '3month', '1month', '7days']

    # Period display names
    PERIOD_NAMES = {
        'overall': 'All Time',
        '12month': 'Last 12 Months',
        '6month': 'Last 6 Months',
        '3month': 'Last 3 Months',
        '1month': 'Last Month',
        '7days': 'Last 7 Days'
    }

    def __init__(self, db: Database, lastfm: LastfmClient, discogs, config: Config):
        self.db = db
        self.lastfm = lastfm
        self.discogs = discogs
        self.config = config
        self.pagination = PaginationManager()

    @staticmethod
    def normalize_command(cmd: str) -> str:
        """Convert command abbreviations to full names."""
        return CommandHandler.COMMAND_ALIASES.get(cmd.lower(), cmd.lower())

    @staticmethod
    def normalize_period(period: str) -> str:
        """Convert period abbreviations to full names."""
        return CommandHandler.PERIOD_ALIASES.get(period.lower(), period.lower())

    async def _get_target_user(self, room: MatrixRoom, sender: str, client: AsyncClient, args: list = None) -> Optional[str]:
        """Get Last.fm username for a user, with error handling."""
        if args and args[0]:
            return args[0]

        target_user = await self.db.get_lastfm_username(sender)
        if not target_user:
            await self.send_message(
                room,
                f"❌ You haven't linked a Last.fm account. Use `{self.config.command_prefix}fm link <username>`",
                client
            )
        return target_user

    async def _validate_period(self, room: MatrixRoom, period: str, client: AsyncClient) -> bool:
        """Validate and send error if period is invalid. Returns True if valid."""
        if period in self.VALID_PERIODS:
            return True

        await self.send_message(
            room,
            f"❌ Invalid period '{period}'. Valid options: {', '.join(self.VALID_PERIODS)}",
            client
        )
        return False

    @staticmethod
    def _extract_artist_name(artist) -> str:
        """Extract artist name from dict or string."""
        if isinstance(artist, dict):
            # Last.fm API uses both 'name' and '#text' fields
            return artist.get('name') or artist.get('#text', 'Unknown')
        return str(artist) if artist else 'Unknown'

    def _get_period_name(self, period: str) -> str:
        """Get display name for a period."""
        return self.PERIOD_NAMES.get(period, period)

    async def handle_command(self, room: MatrixRoom, sender: str, message: str, client: AsyncClient):
        """Parse and handle command."""
        try:
            # Parse command
            logger.info(f"Raw message: '{message}'")
            parts = message[len(self.config.command_prefix):].split()
            logger.info(f"Parts after split: {parts}")
            if not parts:
                return

            command = self.normalize_command(parts[0].lower())
            args = parts[1:]
            logger.info(f"Normalized command: '{command}', args: {args}")

            # Route to appropriate handler
            if command == 'help':
                await self.show_help(room, client)
            elif command == 'lastfm':
                if not args:
                    await self.show_now_playing(room, sender, client)
                elif self.normalize_command(args[0]) == 'help':
                    await self.show_help(room, client)
                elif self.normalize_command(args[0]) == 'link':
                    await self.link_user(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'authcomplete':
                    await self.complete_auth_flow(room, sender, client)
                elif self.normalize_command(args[0]) == 'sessionkey':
                    await self.set_session_key(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'stats':
                    await self.show_stats(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'topalbums':
                    await self.show_top_albums(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'topartists':
                    await self.show_top_artists(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'toptracks':
                    await self.show_top_tracks(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'recent':
                    await self.show_recent_tracks(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'track':
                    await self.show_track_info(room, args[1:], client)
                elif self.normalize_command(args[0]) == 'love':
                    await self.love_track_command(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'unlove':
                    await self.unlove_track_command(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'loved':
                    await self.show_loved_tracks(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'whoknows':
                    await self.who_knows(room, args[1:], client)
                elif self.normalize_command(args[0]) == 'whoknowstrack':
                    await self.who_knows_track(room, args[1:], client)
                elif self.normalize_command(args[0]) == 'whoknowsalbum':
                    await self.who_knows_album(room, args[1:], client)
                elif self.normalize_command(args[0]) == 'chart':
                    await self.generate_chart(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'leaderboard':
                    await self.show_leaderboard(room, args[1:], client)
                else:
                    await self.send_message(room, f"Unknown command: {args[0]}", client)
            elif command == 'discogs':
                if not self.discogs:
                    await self.send_message(room, "❌ Discogs integration is not configured.", client)
                    return

                if not args:
                    await self.show_discogs_help(room, client)
                elif self.normalize_command(args[0]) == 'help':
                    await self.show_discogs_help(room, client)
                elif self.normalize_command(args[0]) == 'link':
                    await self.link_discogs_user(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'stats':
                    await self.show_discogs_stats(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'collection':
                    await self.show_discogs_collection(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'wantlist':
                    await self.show_discogs_wantlist(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'search':
                    await self.search_discogs(room, args[1:], client)
                elif self.normalize_command(args[0]) == 'artist':
                    await self.show_discogs_artist(room, args[1:], client)
                elif self.normalize_command(args[0]) == 'release':
                    await self.show_discogs_release(room, args[1:], client)
                else:
                    await self.send_message(room, f"Unknown Discogs command: {args[0]}", client)
            else:
                await self.send_message(room, f"Unknown command. Type `{self.config.command_prefix}fm help` for help.", client)

        except Exception as e:
            logger.error(f"Error handling command: {e}", exc_info=True)
            await self.send_message(room, f"Error processing command: {str(e)}", client)

    async def handle_reaction(self, room: MatrixRoom, event, sender: str, client: AsyncClient):
        """Handle reaction events for pagination."""
        try:
            logger.info(f"Reaction event received - sender: {sender}")

            # Get the event being reacted to from ReactionEvent
            reacted_event_id = event.reacts_to
            reaction_key = event.key

            logger.info(f"Reacted event ID: {reacted_event_id}, Reaction key: {reaction_key}")

            if not reacted_event_id or not reaction_key:
                logger.info("Missing event ID or reaction key, skipping")
                return

            # Check if this is a paginated message
            pagination = self.pagination.get(reacted_event_id)
            logger.info(f"Pagination state: {pagination}")
            logger.info(f"All paginations: {list(self.pagination.paginations.keys())}")

            if not pagination:
                logger.info("Not a paginated message, skipping")
                return

            # Verify the reactor is the original user
            if sender != pagination['user_id']:
                logger.info(f"Reactor {sender} is not the original user {pagination['user_id']}, skipping")
                return

            # Handle navigation
            current_page = pagination['current_page']
            total_pages = pagination['total_pages']
            new_page = current_page

            logger.info(f"Current page: {current_page}, Total pages: {total_pages}, Reaction: {reaction_key}")

            if reaction_key == "⬅️" and current_page > 1:
                new_page = current_page - 1
            elif reaction_key == "➡️" and current_page < total_pages:
                new_page = current_page + 1
            else:
                logger.info(f"Reaction {reaction_key} not applicable for current page")
                return

            logger.info(f"Navigating to page {new_page}")

            # Update page in memory
            self.pagination.update_page(reacted_event_id, new_page)

            # Call the callback to generate new content
            callback = pagination['callback']
            new_content = await callback(new_page)

            # Delete the old message
            import asyncio
            try:
                await client.room_redact(room.room_id, reacted_event_id)
                logger.info(f"Deleted message {reacted_event_id}")
            except Exception as e:
                logger.warning(f"Could not delete message: {e}")

            await asyncio.sleep(0.1)

            # Send new message with fresh reactions
            new_event_id = await self.send_message(room, new_content, client)
            logger.info(f"Sent new message {new_event_id}")

            if new_event_id:
                # Update pagination to track new message
                if reacted_event_id in self.pagination.paginations:
                    old_pagination = self.pagination.paginations.pop(reacted_event_id)
                    self.pagination.paginations[new_event_id] = {
                        'room_id': old_pagination['room_id'],
                        'user_id': old_pagination['user_id'],
                        'current_page': new_page,
                        'total_pages': old_pagination['total_pages'],
                        'callback': old_pagination['callback'],
                        'reaction_event_ids': []
                    }

                # Add fresh reactions
                await asyncio.sleep(0.1)
                await client.room_send(
                    room_id=room.room_id,
                    message_type="m.reaction",
                    content={"m.relates_to": {"rel_type": "m.annotation", "event_id": new_event_id, "key": "⬅️"}}
                )
                await client.room_send(
                    room_id=room.room_id,
                    message_type="m.reaction",
                    content={"m.relates_to": {"rel_type": "m.annotation", "event_id": new_event_id, "key": "➡️"}}
                )
                logger.info(f"Added fresh reactions to {new_event_id}")

        except Exception as e:
            logger.error(f"Error handling reaction: {e}", exc_info=True)

    async def show_help(self, room: MatrixRoom, client: AsyncClient):
        """Show help message."""
        discogs_info = ""
        if self.discogs:
            discogs_info = f"\n\n**Discogs Integration:**\nUse `{self.config.command_prefix}discogs help` (dg help) for Discogs commands"

        help_text = f"""
FMatrix Bot - Last.fm Stats & Leaderboards

**Main Commands:**
`{self.config.command_prefix}fm` - Show now playing track (or `{self.config.command_prefix}fm <username>`)
`{self.config.command_prefix}fm link <username>` (l) - Link Last.fm account & start authorization
`{self.config.command_prefix}fm authcomplete` - Complete authorization after visiting auth link
`{self.config.command_prefix}fm stats` (s) - Show listening stats
`{self.config.command_prefix}fm topartists [period]` (tar) - Show top artists
`{self.config.command_prefix}fm topalbums [period]` (ta/tb) - Show top albums
`{self.config.command_prefix}fm toptracks [period]` (tt) - Show top tracks
`{self.config.command_prefix}fm recent` (r) - Show recent tracks

**Track Commands:**
`{self.config.command_prefix}fm track <artist> - <track>` - Show track info and playcount
`{self.config.command_prefix}fm loved [username] [limit]` - Show user's loved tracks
`{self.config.command_prefix}fm love <artist> - <track>` - Love a track (requires session key)
`{self.config.command_prefix}fm unlove <artist> - <track>` - Unlove a track (requires session key)

**Room Commands:**
`{self.config.command_prefix}fm whoknows <artist>` (wk) - Who in this room knows this artist
`{self.config.command_prefix}fm whoknowstrack <track>` (wkt) - Who in this room knows this track
`{self.config.command_prefix}fm whoknowsalbum <album>` (wka) - Who in this room knows this album
`{self.config.command_prefix}fm chart [size] [period] [flags]` (c) - Generate album collage
  Flags: `--skipempty/-s` (skip empty albums), `--notitles/-n` (hide album titles)
`{self.config.command_prefix}fm leaderboard [stat]` (lb) - Show room leaderboard

`{self.config.command_prefix}fm help` (?) - Show this help

**Period Abbreviations:**
7days/7d, 1month/1m, 3month/3m, 6month/6m, 12month/12m, overall

**Setup for Love/Unlove (One Command!):**
1. Run: `{self.config.command_prefix}fm link <your_lastfm_username>`
2. Click the auth link
3. Authorize on Last.fm
4. Run: `{self.config.command_prefix}fm authcomplete`
Done! ✅

**Examples:**
`{self.config.command_prefix}fm` - Your now playing track
`{self.config.command_prefix}fm link PlaylistNinja2000` - Link & start auth
`{self.config.command_prefix}fm ta 7d` - Top albums last 7 days
`{self.config.command_prefix}fm track The Beatles - Hey Jude` - Get Hey Jude info
`{self.config.command_prefix}fm loved` - Show your loved tracks
`{self.config.command_prefix}fm love Black Sabbath - Iron Man` - Love Iron Man

**GitHub:** [Source Code](https://github.com/zerw0/fmatrix){discogs_info}
        """
        await self.send_message(room, help_text, client)

    async def link_user(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Link a Matrix user to a Last.fm account and start auth flow."""
        logger.info(f"link_user called - sender type: {type(sender).__name__}, sender value: '{sender}'")
        if not args:
            await self.send_message(room, f"Usage: `{self.config.command_prefix}fm link <username>`", client)
            return

        lastfm_username = args[0]

        # Verify the username exists on Last.fm
        logger.info(f"Linking {sender} to Last.fm user {lastfm_username}")
        user_info = await self.lastfm.get_user_info(lastfm_username)
        if not user_info:
            await self.send_message(room, f"❌ Last.fm user '{lastfm_username}' not found", client)
            return

        # Store the mapping
        success = await self.db.link_user(sender, lastfm_username)
        if not success:
            await self.send_message(room, "❌ Failed to link account", client)
            return
        logger.info(f"Successfully linked {sender} to {lastfm_username}")

        # Verify the link was saved
        saved_username = await self.db.get_lastfm_username(sender)
        logger.info(f"Verification: {sender} is linked to {saved_username}")

        await self.send_message(
            room,
            f"✅ Linked {sender} to Last.fm user **{lastfm_username}**\n\n� Sending authorization link via DM...",
            client
        )

        # Now start the auth flow automatically
        try:
            logger.info(f"Starting auth flow for {sender}")
            # Get an auth token
            auth_token = await self.lastfm.get_auth_token()
            if not auth_token:
                logger.error("Failed to get auth token from Last.fm")
                await self.send_message(
                    room,
                    "❌ Failed to get auth token from Last.fm. Please try again later.",
                    client
                )
                return

            # Store the token
            success = await self.db.store_auth_token(sender, auth_token)
            if not success:
                await self.send_message(room, "❌ Failed to store auth token", client)
                return

            # Get auth URL
            auth_url = self.lastfm.get_auth_url(auth_token)
            # Send authorization instructions via DM
            logger.info(f"Preparing DM message for {sender}")
            message = f"""🔐 **Last.fm Authorization Required**

👉 **Click here to authorize:** {auth_url}

**Steps:**
1. Click the link above
2. Click "Allow" on Last.fm to authorize this bot
3. Come back to this room and run: `{self.config.command_prefix}fm authcomplete`
4. Done! Your love/unlove commands will work

*(Token expires in 10 minutes)*"""

            # Send via DM
            sent_dm = False
            try:
                # Ensure sender has full user ID format
                user_id = sender if sender.startswith("@") else f"@{sender}:{client.homeserver.split('https://')[-1]}"
                logger.info(f"Attempting to send DM to {sender} with full ID: {user_id}")

                # Create DM room
                from nio.responses import RoomCreateResponse
                dm_response = await client.room_create(
                    is_direct=True,
                    invite=[user_id]
                )

                logger.info(f"DM room creation response type: {type(dm_response).__name__}")

                if isinstance(dm_response, RoomCreateResponse):
                    dm_room_id = dm_response.room_id
                    logger.info(f"Created DM room: {dm_room_id}")

                    # Send the message to the DM
                    send_response = await client.room_send(
                        dm_room_id,
                        "m.room.message",
                        {
                            "msgtype": "m.text",
                            "body": message
                        }
                    )
                    logger.info(f"DM message send response type: {type(send_response).__name__}")
                    sent_dm = True
                    await self.send_message(room, "✅ Authorization link sent via DM!", client)
                else:
                    logger.warning(f"Unexpected response type from room_create: {type(dm_response).__name__} - {dm_response}")
                    await self.send_message(room, f"⚠️ Could not send DM. Showing auth link here:", client)
                    await self.send_message(room, message, client)
            except Exception as dm_error:
                logger.error(f"Failed to send DM: {dm_error}", exc_info=True)
                await self.send_message(room, f"⚠️ Could not send DM. Showing auth link here:", client)
                await self.send_message(room, message, client)

        except Exception as e:
            logger.error(f"Error in auth flow: {e}", exc_info=True)
            await self.send_message(room, f"❌ Error: {str(e)}", client)

    async def set_session_key(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Set Last.fm session key for authenticated commands."""
        if not args:
            await self.send_message(
                room,
                f"Usage: `{self.config.command_prefix}fm sessionkey <your_session_key>`\n\nGet your session key from: https://www.last.fm/api/auth",
                client
            )
            return

        session_key = args[0]

        # Store the session key
        success = await self.db.set_lastfm_session_key(sender, session_key)
        if success:
            await self.send_message(
                room,
                f"✅ Session key saved. You can now use love/unlove commands!",
                client
            )
        else:
            await self.send_message(room, "❌ Failed to save session key", client)

    async def start_auth_flow(self, room: MatrixRoom, sender: str, client: AsyncClient):
        """Start Last.fm authorization flow."""
        try:
            # Get an auth token
            auth_token = await self.lastfm.get_auth_token()
            if not auth_token:
                await self.send_message(
                    room,
                    "❌ Failed to get auth token from Last.fm. Please try again later.",
                    client
                )
                return

            # Store the token
            success = await self.db.store_auth_token(sender, auth_token)
            if not success:
                await self.send_message(room, "❌ Failed to store auth token", client)
                return

            # Get auth URL
            auth_url = self.lastfm.get_auth_url(auth_token)

            # Send DM with instructions
            try:
                # Try to send a DM (Matrix doesn't have traditional DMs, but we can mention them)
                message = f"""
🔐 **Last.fm Authorization Flow**

1️⃣ Click this link to authorize: {auth_url}

2️⃣ After authorizing, come back and run:
`{self.config.command_prefix}fm authcomplete`

3️⃣ Your session key will be saved automatically!

The token expires in 10 minutes.
                """
                await self.send_message(room, message, client)
            except Exception as e:
                logger.error(f"Error sending message: {e}")
                await self.send_message(room, "❌ Failed to send message", client)

        except Exception as e:
            logger.error(f"Error in auth flow: {e}", exc_info=True)
            await self.send_message(room, f"❌ Error: {str(e)}", client)

    async def complete_auth_flow(self, room: MatrixRoom, sender: str, client: AsyncClient):
        """Complete Last.fm authorization by exchanging token for session key."""
        try:
            logger.info(f"complete_auth_flow called - sender type: {type(sender).__name__}, sender value: '{sender}'")

            # Get the stored auth token
            auth_token = await self.db.get_auth_token(sender)
            if not auth_token:
                logger.warning(f"No auth token found for {sender}")
                await self.send_message(
                    room,
                    f"❌ No pending auth token. Run `{self.config.command_prefix}fm link` first.",
                    client
                )
                return

            logger.info(f"Found auth token for {sender}, exchanging for session key")

            # Exchange token for session key
            session_key = await self.lastfm.get_session_from_token(auth_token)
            if not session_key:
                logger.error(f"Failed to get session key for {sender}")
                await self.send_message(
                    room,
                    "❌ Failed to get session key. Make sure you authorized the app.",
                    client
                )
                return

            logger.info(f"Got session key for {sender}, saving to database")

            # Save the session key
            success = await self.db.set_lastfm_session_key(sender, session_key)
            if not success:
                logger.error(f"Failed to save session key for {sender}")
                await self.send_message(room, "❌ Failed to save session key", client)
                return

            logger.info(f"Session key saved successfully for {sender}")

            # Delete the auth token
            await self.db.delete_auth_token(sender)

            # Success!
            await self.send_message(
                room,
                f"✅ Authorization successful! Session key saved.\n\n🎵 You can now use:\n`{self.config.command_prefix}fm love <artist> - <track>`\n`{self.config.command_prefix}fm unlove <artist> - <track>`",
                client
            )

        except Exception as e:
            logger.error(f"Error completing auth: {e}", exc_info=True)
            await self.send_message(room, f"❌ Error: {str(e)}", client)

    async def show_stats(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Show user's Last.fm stats."""
        target_user = await self._get_target_user(room, sender, client, args)
        if not target_user:
            return

        # Get stats
        stats = await self.lastfm.get_user_stats(target_user)
        if not stats:
            await self.send_message(room, f"❌ Could not fetch stats for {target_user}", client)
            return

        # Format message
        message = f"""
**{stats['username']}**'s Last.fm Stats:
📊 Scrobbles: {stats['play_count']:,}
🎤 Artists: {stats['artist_count']:,}
🎵 Tracks: {stats['track_count']:,}
💿 Albums: {stats['album_count']:,}
        """

        await self.send_message(room, message, client)

    async def show_top_artists(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Show user's top artists."""
        period = self.normalize_period(args[0].lower()) if args else 'overall'

        if not await self._validate_period(room, period, client):
            return

        target_user = await self._get_target_user(room, sender, client)
        if not target_user:
            return

        artists = await self.lastfm.get_top_artists(target_user, period, limit=10)
        if not artists:
            await self.send_message(room, f"❌ Could not fetch top artists for {target_user}", client)
            return

        period_name = self._get_period_name(period)
        message = f"**Top Artists ({period_name})**\n\n"

        for i, artist in enumerate(artists, 1):
            name = artist.get('name', 'Unknown')
            plays = artist.get('playcount', '0')
            message += f"{i}. {name} - {plays} plays\n"

        await self.send_message(room, message, client)

    async def show_top_albums(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Show user's top albums."""
        period = self.normalize_period(args[0].lower()) if args else 'overall'

        if not await self._validate_period(room, period, client):
            return

        target_user = await self._get_target_user(room, sender, client)
        if not target_user:
            return

        albums = await self.lastfm.get_top_albums(target_user, period, limit=10)
        if not albums:
            await self.send_message(room, f"❌ Could not fetch top albums", client)
            return

        period_name = self._get_period_name(period)
        message = f"**Top Albums ({period_name})**\n\n"

        for i, album in enumerate(albums, 1):
            name = album.get('name', 'Unknown')
            artist_name = self._extract_artist_name(album.get('artist', {}))
            plays = album.get('playcount', '0')
            message += f"{i}. {name} by {artist_name} - {plays} plays\n"

        await self.send_message(room, message, client)

    async def show_top_tracks(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Show user's top tracks."""
        period = self.normalize_period(args[0].lower()) if args else 'overall'

        if not await self._validate_period(room, period, client):
            return

        target_user = await self._get_target_user(room, sender, client)
        if not target_user:
            return

        tracks = await self.lastfm.get_top_tracks(target_user, period, limit=10)
        if not tracks:
            await self.send_message(room, f"❌ Could not fetch top tracks", client)
            return

        period_name = self._get_period_name(period)
        message = f"**Top Tracks ({period_name})**\n\n"

        for i, track in enumerate(tracks, 1):
            name = track.get('name', 'Unknown')
            artist_name = self._extract_artist_name(track.get('artist', {}))
            plays = track.get('playcount', '0')
            message += f"{i}. {name} by {artist_name} - {plays} plays\n"

        await self.send_message(room, message, client)

    async def show_recent_tracks(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Show user's recent tracks."""
        target_user = await self._get_target_user(room, sender, client)
        if not target_user:
            return

        tracks = await self.lastfm.get_recent_tracks(target_user, limit=7)
        if not tracks:
            await self.send_message(room, f"❌ Could not fetch recent tracks", client)
            return

        message = f"**Recent Tracks - {target_user}**\n\n"

        for i, track in enumerate(tracks, 1):
            name = track.get('name', 'Unknown')
            artist_name = self._extract_artist_name(track.get('artist', {}))
            message += f"{i}. {name} by {artist_name}\n"

        await self.send_message(room, message, client)

    async def show_now_playing(self, room: MatrixRoom, sender: str, client: AsyncClient):
        """Show user's currently playing track."""
        target_user = await self._get_target_user(room, sender, client)
        if not target_user:
            return

        track = await self.lastfm.get_now_playing(target_user)
        if not track:
            await self.send_message(room, f"❌ Could not fetch now playing track for {target_user}", client)
            return

        name = track.get('name', 'Unknown')
        artist_name = self._extract_artist_name(track.get('artist', {}))
        album = track.get('album', {})
        album_name = album.get('text', 'Unknown') if isinstance(album, dict) else album or 'Unknown'
        play_count = track.get('userplaycount', 'N/A')

        message = f"🎵 **Now Playing - {target_user}**\n\n"
        message += f"**{name}**\n"
        message += f"by *{artist_name}*\n"
        message += f"on {album_name}\n"
        message += f"Plays: {play_count}"

        await self.send_message(room, message, client)

    async def show_track_info(self, room: MatrixRoom, args: list, client: AsyncClient):
        """Show track information including playcount."""
        if len(args) < 2:
            await self.send_message(
                room,
                f"❌ Usage: {self.config.command_prefix}track <artist> - <track>",
                client
            )
            return

        # Find the dash separator
        try:
            dash_idx = args.index('-')
            artist_name = ' '.join(args[:dash_idx])
            track_name = ' '.join(args[dash_idx + 1:])
        except ValueError:
            await self.send_message(
                room,
                f"❌ Usage: {self.config.command_prefix}track <artist> - <track>",
                client
            )
            return

        track_info = await self.lastfm.get_track_info(artist_name, track_name)
        if not track_info:
            await self.send_message(room, f"❌ Could not find track: {track_name} by {artist_name}", client)
            return

        name = track_info.get('name', 'Unknown')
        artist = track_info.get('artist', {})
        artist_name = artist.get('name', 'Unknown') if isinstance(artist, dict) else artist or 'Unknown'
        listeners = track_info.get('listeners', 'N/A')
        plays = track_info.get('playcount', 'N/A')
        tags = track_info.get('toptags', {})
        tag_list = tags.get('tag', []) if isinstance(tags, dict) else []
        tag_str = ', '.join([t.get('name', '') for t in tag_list[:5]]) if tag_list else 'No tags'

        message = f"🎵 **Track Info: {name}**\n\n"
        message += f"**Artist:** {artist_name}\n"
        message += f"**Listeners:** {listeners}\n"
        message += f"**Total Plays:** {plays}\n"
        message += f"**Tags:** {tag_str}"

        await self.send_message(room, message, client)

    async def show_loved_tracks(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Show user's loved tracks."""
        target_user = await self._get_target_user(room, sender, client, args)
        if not target_user:
            return

        limit = 10
        if args and args[0].isdigit():
            limit = min(int(args[0]), 50)

        tracks = await self.lastfm.get_user_loved_tracks(target_user, limit=limit)
        if not tracks:
            await self.send_message(room, f"❌ Could not fetch loved tracks for {target_user}", client)
            return

        message = f"❤️ **Loved Tracks - {target_user}**\n\n"

        for i, track in enumerate(tracks, 1):
            name = track.get('name', 'Unknown')
            artist_name = self._extract_artist_name(track.get('artist', {}))
            message += f"{i}. {name} by {artist_name}\n"

        await self.send_message(room, message, client)

    async def love_track_command(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Love a track (requires session key)."""
        logger.info(f"love_track_command called with args: {args}")
        if len(args) < 2:
            await self.send_message(
                room,
                f"❌ Usage: {self.config.command_prefix}fm love <artist> - <track>",
                client
            )
            return

        # Find the dash separator
        try:
            dash_idx = args.index('-')
            artist_name = ' '.join(args[:dash_idx])
            track_name = ' '.join(args[dash_idx + 1:])
            logger.info(f"Parsed: artist='{artist_name}', track='{track_name}'")
        except ValueError:
            await self.send_message(
                room,
                f"❌ Usage: {self.config.command_prefix}fm love <artist> - <track>",
                client
            )
            return

        # Get session key
        session_key = await self.db.get_lastfm_session_key(sender)
        logger.info(f"Got session key for {sender}: {bool(session_key)}")
        if not session_key:
            await self.send_message(
                room,
                f"❌ You don't have a Last.fm session key. This feature requires Last.fm authentication.",
                client
            )
            return

        # Love the track
        logger.info(f"Calling love_track with artist='{artist_name}', track='{track_name}', session_key=***")
        success = await self.lastfm.love_track(artist_name, track_name, session_key)
        if success:
            await self.send_message(
                room,
                f"❤️ Loved: **{track_name}** by {artist_name}",
                client
            )
        else:
            await self.send_message(
                room,
                f"❌ Failed to love track: {track_name} by {artist_name}",
                client
            )

    async def unlove_track_command(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Unlove a track (requires session key)."""
        logger.info(f"unlove_track_command called with args: {args}")
        if len(args) < 2:
            await self.send_message(
                room,
                f"❌ Usage: {self.config.command_prefix}fm unlove <artist> - <track>",
                client
            )
            return

        # Find the dash separator
        try:
            dash_idx = args.index('-')
            artist_name = ' '.join(args[:dash_idx])
            track_name = ' '.join(args[dash_idx + 1:])
            logger.info(f"Parsed unlove: artist='{artist_name}', track='{track_name}'")
        except ValueError:
            await self.send_message(
                room,
                f"❌ Usage: {self.config.command_prefix}fm unlove <artist> - <track>",
                client
            )
            return

        # Get session key
        session_key = await self.db.get_lastfm_session_key(sender)
        logger.info(f"Got session key for {sender}: {bool(session_key)}")
        if not session_key:
            await self.send_message(
                room,
                f"❌ You don't have a Last.fm session key. This feature requires Last.fm authentication.",
                client
            )
            return

        # Unlove the track
        logger.info(f"Calling unlove_track with artist='{artist_name}', track='{track_name}', session_key=***")
        success = await self.lastfm.unlove_track(artist_name, track_name, session_key)
        if success:
            await self.send_message(
                room,
                f"💔 Unloved: **{track_name}** by {artist_name}",
                client
            )
        else:
            await self.send_message(
                room,
                f"❌ Failed to unlove track: {track_name} by {artist_name}",
                client
            )



    async def show_leaderboard(self, room: MatrixRoom, args: list, client: AsyncClient):
        """Show leaderboard of room members' Last.fm stats."""
        stat_type = args[0] if args else 'playcounts'

        # Get all room members
        room_members = list(room.users.keys())
        if not room_members:
            await self.send_message(room, "❌ No members in room", client)
            return

        # Get Last.fm usernames for all members
        user_mapping = await self.db.get_all_users_in_room(room.room_id, room_members)
        if not user_mapping:
            await self.send_message(room, "❌ No one in this room has linked a Last.fm account", client)
            return

        # Fetch stats
        leaderboard_data = []
        for matrix_user, lastfm_user in user_mapping.items():
            stats = await self.lastfm.get_user_stats(lastfm_user)
            if stats:
                leaderboard_data.append({
                    'lastfm': lastfm_user,
                    'stats': stats
                })

        if not leaderboard_data:
            await self.send_message(room, "❌ Could not fetch stats", client)
            return

        # Sort by stat type
        if stat_type == 'playcounts':
            leaderboard_data.sort(key=lambda x: x['stats']['play_count'], reverse=True)
            stat_display = "Scrobbles"
            stat_key = 'play_count'
        elif stat_type == 'artistcount':
            leaderboard_data.sort(key=lambda x: x['stats']['artist_count'], reverse=True)
            stat_display = "Artists"
            stat_key = 'artist_count'
        elif stat_type == 'trackcount':
            leaderboard_data.sort(key=lambda x: x['stats']['track_count'], reverse=True)
            stat_display = "Tracks"
            stat_key = 'track_count'
        else:
            await self.send_message(room, f"❌ Unknown stat type. Use: playcounts, artistcount, trackcount", client)
            return

        # Build message
        message = f"**🏆 Room Leaderboard - {stat_display}**\n\n"
        medals = ['🥇', '🥈', '🥉']

        for i, entry in enumerate(leaderboard_data[:10], 1):
            medal = medals[i-1] if i <= 3 else f"{i}."
            username = entry['lastfm']
            stat_value = entry['stats'][stat_key]
            message += f"{medal} {username}: {stat_value:,}\n"

        await self.send_message(room, message, client)

    async def who_knows(self, room: MatrixRoom, args: list, client: AsyncClient):
        """Show who in the room listens to this artist."""
        if not args:
            await self.send_message(room, f"Usage: `{self.config.command_prefix}fm whoknows <artist name>`", client)
            return

        artist_name = ' '.join(args)
        artists = await self.lastfm.search_artist(artist_name, limit=1)

        if not artists:
            await self.send_message(room, f"❌ No artists found matching '{artist_name}'", client)
            return

        # Get the top result
        top_artist = artists[0]
        artist_name_clean = top_artist.get('name', artist_name)

        # Try to get image from search results first (as fallback)
        search_image = None
        search_image_list = top_artist.get('image', [])
        placeholder_hashes = ['2a96cbd8b46e442fc41c2b86b821562f']

        if isinstance(search_image_list, list) and len(search_image_list) > 0:
            # Get 'large' size (174s) instead of extralarge for more compact display
            for img in search_image_list:
                if img.get('size') == 'large':
                    img_url = img.get('#text', '').strip()
                    if img_url and '/noimage' not in img_url.lower():
                        is_placeholder = any(placeholder_hash in img_url for placeholder_hash in placeholder_hashes)
                        if not is_placeholder:
                            search_image = img_url
                            break

        # Get detailed info
        artist_info = await self.lastfm.get_artist_info(artist_name_clean)

        if not artist_info:
            await self.send_message(room, f"❌ Could not fetch details for {artist_name_clean}", client)
            return

        # Get genre
        tags = artist_info.get('tags', {})
        if isinstance(tags, dict) and 'tag' in tags:
            genre_list = tags['tag']
            if isinstance(genre_list, list):
                genre = ', '.join([t.get('name', '') for t in genre_list[:3]])
            else:
                genre = genre_list.get('name', 'Unknown')
        else:
            genre = 'Unknown'

        # Get image - extract the extralarge/largest available
        image = None
        image_list = artist_info.get('image', [])

        # Last.fm's known placeholder image hashes to filter out
        placeholder_hashes = ['2a96cbd8b46e442fc41c2b86b821562f']

        logger.info(f"Image list for {artist_name_clean}: {image_list}")

        if isinstance(image_list, list) and len(image_list) > 0:
            # Get 'large' size (174s) for more compact display
            for img in image_list:
                if img.get('size') == 'large':
                    img_url = img.get('#text', '').strip()
                    if img_url and img_url != '' and '/noimage' not in img_url.lower():
                        is_placeholder = any(placeholder_hash in img_url for placeholder_hash in placeholder_hashes)
                        if not is_placeholder:
                            image = img_url
                            logger.info(f"Found valid image: {image}")
                            break
                        else:
                            logger.info(f"Skipping placeholder image: {img_url}")

        if not image:
            logger.info(f"No valid image found for {artist_name_clean} in artist info")
            # Fall back to search image
            if search_image:
                image = search_image
                logger.info(f"Using image from search results: {image}")
            else:
                # Try to get image from artist's top album as last resort
                logger.info(f"Trying to fetch image from top album for {artist_name_clean}")
                top_albums = await self.lastfm.get_artist_top_albums(artist_name_clean, limit=1)
                if top_albums and len(top_albums) > 0:
                    album_image_list = top_albums[0].get('image', [])
                    if isinstance(album_image_list, list):
                        for img in album_image_list:
                            if img.get('size') == 'large':
                                img_url = img.get('#text', '').strip()
                                if img_url and '/noimage' not in img_url.lower():
                                    is_placeholder = any(h in img_url for h in placeholder_hashes)
                                    if not is_placeholder:
                                        image = img_url
                                        logger.info(f"Using image from top album: {image}")
                                        break

                if not image:
                    logger.info(f"No valid image available for {artist_name_clean} at all")

        # Get artist listeners and stats
        listeners = artist_info.get('stats', {}).get('listeners', 'N/A')
        scrobbles = artist_info.get('stats', {}).get('playcount', 'N/A')

        # Format numbers safely
        try:
            listeners_formatted = f"{int(listeners):,}" if listeners != 'N/A' else 'N/A'
        except (ValueError, TypeError):
            listeners_formatted = 'N/A'

        try:
            scrobbles_formatted = f"{int(scrobbles):,}" if scrobbles != 'N/A' else 'N/A'
        except (ValueError, TypeError):
            scrobbles_formatted = 'N/A'

        # Build embed with artist leaderboard
        room_members = list(room.users.keys())
        user_mapping = await self.db.get_all_users_in_room(room.room_id, room_members)

        # Fetch top listeners in this room for this artist
        room_listeners = []
        for matrix_user, lastfm_user in user_mapping.items():
            try:
                top_artists = await self.lastfm.get_all_top_artists(lastfm_user, period='overall')
                for artist in top_artists:
                    if artist.get('name', '').lower() == artist_name_clean.lower():
                        room_listeners.append({
                            'user': lastfm_user,
                            'plays': int(artist.get('playcount', 0))
                        })
                        break
            except:
                pass

        room_listeners.sort(key=lambda x: x['plays'], reverse=True)

        # Send image as separate message (downloaded from Last.fm and uploaded to Matrix)
        # Only if we have a valid non-placeholder image
        if image and '/noimage' not in image.lower() and '2a96cbd8b46e442fc41c2b86b821562f' not in image:
            logger.info(f"Downloading and uploading image: {image}")
            await self.send_image_message(room, image, artist_name_clean, client)
        else:
            logger.info(f"No valid image to display for {artist_name_clean}")

        # Build minimal HTML embed
        html_parts = []
        artist_url = f"https://www.last.fm/music/{artist_name_clean.replace(' ', '+')}"
        html_parts.append(f"<b><a href='{artist_url}'>{artist_name_clean}</a></b>")
        html_parts.append(f"<br/>{genre}")

        # Add leaderboard
        if room_listeners:
            html_parts.append("<br/>")
            medals = ['👑', '🥈', '🥉']
            for i, listener in enumerate(room_listeners[:5], 1):  # Show top 5 only
                medal = medals[i-1] if i <= 3 else f"{i}."
                user_url = f"https://www.last.fm/user/{listener['user']}"
                html_parts.append(f"<br/>{medal} <a href='{user_url}'>{listener['user']}</a> · {listener['plays']:,}")

        html = "\n".join(html_parts)

        # Build plain text version
        artist_url = f"https://www.last.fm/music/{artist_name_clean.replace(' ', '+')}"
        body_lines = [f"{artist_name_clean} - {artist_url}", genre, ""]

        if room_listeners:
            medals_text = ['👑', '🥈', '🥉']
            for i, listener in enumerate(room_listeners[:5], 1):  # Show top 5 only
                medal = medals_text[i-1] if i <= 3 else f"{i}."
                user_url = f"https://www.last.fm/user/{listener['user']}"
                body_lines.append(f"{medal} {listener['user']} ({user_url}) · {listener['plays']:,}")
        body = "\n".join(body_lines)

        # Send embed
        await client.room_send(
            room_id=room.room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": body,
                "format": "org.matrix.custom.html",
                "formatted_body": html
            }
        )

    async def who_knows_track(self, room: MatrixRoom, args: list, client: AsyncClient):
        """Show who in the room listens to this track."""
        if not args:
            await self.send_message(room, f"Usage: `{self.config.command_prefix}fm whoknowstrack <track name>`", client)
            return

        track_query = ' '.join(args).lower()

        # Get room members and their Last.fm accounts
        room_members = list(room.users.keys())
        user_mapping = await self.db.get_all_users_in_room(room.room_id, room_members)

        if not user_mapping:
            await self.send_message(room, "❌ No users in this room have linked their Last.fm accounts", client)
            return

        # Fetch top tracks for each user and filter for matches
        room_listeners = []
        for matrix_user, lastfm_user in user_mapping.items():
            try:
                top_tracks = await self.lastfm.get_all_top_tracks(lastfm_user, period='overall')
                for track in top_tracks:
                    track_name = track.get('name', '').lower()
                    if track_query in track_name or track_name in track_query:
                        artist = self._extract_artist_name(track.get('artist', {}))
                        room_listeners.append({
                            'user': lastfm_user,
                            'track': track.get('name', 'Unknown'),
                            'artist': artist,
                            'plays': int(track.get('playcount', 0))
                        })
                        break
            except:
                pass

        if not room_listeners:
            await self.send_message(room, f"❌ No one in this room has listened to '{' '.join(args)}'", client)
            return

        # Sort by plays
        room_listeners.sort(key=lambda x: x['plays'], reverse=True)

        # Build HTML message
        html_parts = []
        first_listener = room_listeners[0]
        track_url = f"https://www.last.fm/music/{first_listener['artist'].replace(' ', '+')}/_/{first_listener['track'].replace(' ', '+')}"
        html_parts.append(f"<b><a href='{track_url}'>{first_listener['track']}</a></b> by {first_listener['artist']}")
        html_parts.append("<br/>")

        medals = ['👑', '🥈', '🥉']
        for i, listener in enumerate(room_listeners[:5], 1):
            medal = medals[i-1] if i <= 3 else f"{i}."
            user_url = f"https://www.last.fm/user/{listener['user']}"
            html_parts.append(f"<br/>{medal} <a href='{user_url}'>{listener['user']}</a> · {listener['plays']:,}")

        html = "\n".join(html_parts)

        # Build plain text version
        body_lines = [f"{first_listener['track']} by {first_listener['artist']} - {track_url}", ""]
        for i, listener in enumerate(room_listeners[:5], 1):
            medal = medals[i-1] if i <= 3 else f"{i}."
            user_url = f"https://www.last.fm/user/{listener['user']}"
            body_lines.append(f"{medal} {listener['user']} ({user_url}) · {listener['plays']:,}")
        body = "\n".join(body_lines)

        await client.room_send(
            room_id=room.room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": body,
                "format": "org.matrix.custom.html",
                "formatted_body": html
            }
        )

    async def who_knows_album(self, room: MatrixRoom, args: list, client: AsyncClient):
        """Show who in the room listens to this album."""
        if not args:
            await self.send_message(room, f"Usage: `{self.config.command_prefix}fm whoknowsalbum <album name>`", client)
            return

        album_query = ' '.join(args).lower()

        # Get room members and their Last.fm accounts
        room_members = list(room.users.keys())
        user_mapping = await self.db.get_all_users_in_room(room.room_id, room_members)

        if not user_mapping:
            await self.send_message(room, "❌ No users in this room have linked their Last.fm accounts", client)
            return

        # Fetch top albums for each user and filter for matches
        room_listeners = []
        for matrix_user, lastfm_user in user_mapping.items():
            try:
                top_albums = await self.lastfm.get_all_top_albums(lastfm_user, period='overall')
                for album in top_albums:
                    album_name = album.get('name', '').lower()
                    if album_query in album_name or album_name in album_query:
                        artist = self._extract_artist_name(album.get('artist', {}))
                        room_listeners.append({
                            'user': lastfm_user,
                            'album': album.get('name', 'Unknown'),
                            'artist': artist,
                            'plays': int(album.get('playcount', 0))
                        })
                        break
            except:
                pass

        if not room_listeners:
            await self.send_message(room, f"❌ No one in this room has listened to '{' '.join(args)}'", client)
            return

        # Sort by plays
        room_listeners.sort(key=lambda x: x['plays'], reverse=True)

        # Build HTML message
        html_parts = []
        first_listener = room_listeners[0]
        album_url = f"https://www.last.fm/music/{first_listener['artist'].replace(' ', '+')}/_/{first_listener['album'].replace(' ', '+')}"
        html_parts.append(f"<b><a href='{album_url}'>{first_listener['album']}</a></b> by {first_listener['artist']}")
        html_parts.append("<br/>")

        medals = ['👑', '🥈', '🥉']
        for i, listener in enumerate(room_listeners[:5], 1):
            medal = medals[i-1] if i <= 3 else f"{i}."
            user_url = f"https://www.last.fm/user/{listener['user']}"
            html_parts.append(f"<br/>{medal} <a href='{user_url}'>{listener['user']}</a> · {listener['plays']:,}")

        html = "\n".join(html_parts)

        # Build plain text version
        body_lines = [f"{first_listener['album']} by {first_listener['artist']} - {album_url}", ""]
        for i, listener in enumerate(room_listeners[:5], 1):
            medal = medals[i-1] if i <= 3 else f"{i}."
            user_url = f"https://www.last.fm/user/{listener['user']}"
            body_lines.append(f"{medal} {listener['user']} ({user_url}) · {listener['plays']:,}")
        body = "\n".join(body_lines)

        await client.room_send(
            room_id=room.room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": body,
                "format": "org.matrix.custom.html",
                "formatted_body": html
            }
        )

    async def generate_chart(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Generate a collage chart of top albums."""
        # Parse arguments: size, period, and flags
        size = '3x3'
        period = '7days'
        skip_empty = False
        show_titles = True

        # Filter out flags
        filtered_args = []
        for arg in args:
            if arg.lower() in ['--skipempty', '--skip-empty', '-s']:
                skip_empty = True
            elif arg.lower() in ['--notitles', '--no-titles', '--notitle', '-n']:
                show_titles = False
            else:
                filtered_args.append(arg)

        if filtered_args:
            # Check if first arg is a size (NxN format)
            if 'x' in filtered_args[0].lower():
                size = filtered_args[0].lower()
                if len(filtered_args) > 1:
                    period = self.normalize_period(filtered_args[1])
            else:
                # First arg is period
                period = self.normalize_period(filtered_args[0])
                if len(filtered_args) > 1 and 'x' in filtered_args[1].lower():
                    size = filtered_args[1].lower()

        # Validate size
        try:
            rows, cols = map(int, size.split('x'))
            if rows < 2 or rows > 10 or cols < 2 or cols > 10:
                await self.send_message(room, "❌ Chart size must be between 2x2 and 10x10", client)
                return
        except:
            await self.send_message(room, f"❌ Invalid size format '{size}'. Use format like 3x3, 4x4, 5x5", client)
            return

        # Validate period
        if not await self._validate_period(room, period, client):
            return

        # Get target user
        lastfm_user = await self._get_target_user(room, sender, client)
        if not lastfm_user:
            return

        await self.send_message(room, f"⏳ Generating {size} chart for {lastfm_user}...", client)

        # Fetch top albums
        total_albums = rows * cols
        albums = await self.lastfm.get_top_albums(lastfm_user, period, limit=total_albums)

        if not albums:
            await self.send_message(room, f"❌ No albums found for {lastfm_user} in this period", client)
            return

        # Download album cover images
        tile_size = 300
        album_tiles = []  # List of (image, album_name, artist_name, has_cover)

        async with aiohttp.ClientSession() as session:
            for album in albums:
                if len(album_tiles) >= total_albums:
                    break

                image_url = None
                album_name = album.get('name', 'Unknown')
                artist_name = self._extract_artist_name(album.get('artist', {}))

                # Get extralarge image (300x300)
                album_images = album.get('image', [])
                if isinstance(album_images, list):
                    for img in album_images:
                        if img.get('size') == 'extralarge':
                            url = img.get('#text', '').strip()
                            if url and '/noimage' not in url.lower():
                                image_url = url
                                break

                # Download image
                if image_url:
                    try:
                        async with session.get(image_url) as resp:
                            if resp.status == 200:
                                image_data = await resp.read()
                                img = Image.open(BytesIO(image_data))
                                img = img.resize((tile_size, tile_size), Image.Resampling.LANCZOS)
                                album_tiles.append((img, album_name, artist_name, True))
                                continue
                    except Exception as e:
                        logger.warning(f"Failed to download album image: {e}")

                # Skip or create placeholder based on flag
                if skip_empty:
                    continue
                else:
                    placeholder = Image.new('RGB', (tile_size, tile_size), color='#1a1a1a')
                    album_tiles.append((placeholder, album_name, artist_name, False))

        # Pad with placeholders if needed (only if not skipping empty)
        if not skip_empty:
            while len(album_tiles) < total_albums:
                placeholder = Image.new('RGB', (tile_size, tile_size), color='#1a1a1a')
                album_tiles.append((placeholder, '', '', False))

        # Adjust grid size if we have fewer items after filtering
        if skip_empty and len(album_tiles) < total_albums:
            actual_count = len(album_tiles)
            # Try to maintain aspect ratio close to original
            cols = min(cols, actual_count)
            rows = (actual_count + cols - 1) // cols

        # Create collage
        collage_width = cols * tile_size
        collage_height = rows * tile_size
        collage = Image.new('RGB', (collage_width, collage_height), color='#0d0d0d')

        # Load font for text overlay
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
        except:
            try:
                # macOS fallback
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
            except:
                font = ImageFont.load_default()

        # Paste album covers with text overlay
        for idx, (img, album_name, artist_name, has_cover) in enumerate(album_tiles):
            if idx >= rows * cols:
                break

            row = idx // cols
            col = idx % cols
            x = col * tile_size
            y = row * tile_size

            # Paste the album cover
            collage.paste(img, (x, y))

            # Add text overlay with artist + album (if enabled)
            if show_titles and (album_name or artist_name):
                draw = ImageDraw.Draw(collage)

                def truncate_to_width(text: str, max_width: int) -> str:
                    if not text:
                        return ''
                    text_bbox = draw.textbbox((0, 0), text, font=font)
                    text_width = text_bbox[2] - text_bbox[0]
                    if text_width <= max_width:
                        return text

                    ellipsis = '...'
                    trimmed = text
                    while trimmed:
                        trimmed = trimmed[:-1]
                        text_bbox = draw.textbbox((0, 0), trimmed + ellipsis, font=font)
                        if (text_bbox[2] - text_bbox[0]) <= max_width:
                            return trimmed + ellipsis
                    return ellipsis

                padding = 5
                line_spacing = 2
                max_text_width = tile_size - (padding * 2)

                lines = []
                if artist_name:
                    lines.append(truncate_to_width(artist_name, max_text_width))
                if album_name:
                    lines.append(truncate_to_width(album_name, max_text_width))

                # Filter out empty lines after truncation
                lines = [line for line in lines if line]
                if lines:
                    line_metrics = []
                    max_line_width = 0
                    total_height = 0
                    for line in lines:
                        text_bbox = draw.textbbox((0, 0), line, font=font)
                        line_width = text_bbox[2] - text_bbox[0]
                        line_height = text_bbox[3] - text_bbox[1]
                        line_metrics.append((line, line_width, line_height))
                        max_line_width = max(max_line_width, line_width)
                        total_height += line_height

                    total_height += line_spacing * (len(lines) - 1)

                    # Position text block at bottom of tile
                    block_x = x + (tile_size - max_line_width) // 2
                    block_y = y + tile_size - total_height - 10

                    # Draw background rectangle
                    draw.rectangle(
                        [
                            block_x - padding,
                            block_y - padding,
                            block_x + max_line_width + padding,
                            block_y + total_height + padding,
                        ],
                        fill=(0, 0, 0, 180)
                    )

                    # Draw each line centered within the block
                    line_y = block_y
                    for line, line_width, line_height in line_metrics:
                        line_x = block_x + (max_line_width - line_width) // 2
                        draw.text((line_x, line_y), line, fill='#FFFFFF', font=font)
                        line_y += line_height + line_spacing

        # Get period name for message
        period_name = self._get_period_name(period)

        # Save to BytesIO
        image_buffer = BytesIO()
        collage.save(image_buffer, format='PNG')
        image_buffer.seek(0)

        # Upload to Matrix
        try:
            upload_response, _ = await client.upload(
                image_buffer,
                content_type='image/png',
                filename=f'{lastfm_user}_{size}_{period}_chart.png',
                filesize=len(image_buffer.getvalue())
            )

            if isinstance(upload_response, UploadResponse):
                await client.room_send(
                    room_id=room.room_id,
                    message_type='m.room.message',
                    content={
                        'msgtype': 'm.image',
                        'body': f'{size} {period_name} chart for {lastfm_user}',
                        'url': upload_response.content_uri,
                        'info': {
                            'mimetype': 'image/png',
                            'size': len(image_buffer.getvalue()),
                            'w': collage_width,
                            'h': collage_height
                        }
                    }
                )
                logger.info(f"Chart sent successfully for {lastfm_user}")
            else:
                await self.send_message(room, f"❌ Failed to upload chart image", client)
                logger.error(f"Upload failed: {upload_response}")
        except Exception as e:
            await self.send_message(room, f"❌ Error generating chart: {str(e)}", client)
            logger.error(f"Chart generation error: {e}", exc_info=True)

    async def send_message(self, room: MatrixRoom, message: str, client: AsyncClient):
        """Send a message to the room."""
        try:
            response = await client.room_send(
                room_id=room.room_id,
                message_type="m.room.message",
                content={
                    "msgtype": "m.text",
                    "body": message,
                    "format": "org.matrix.custom.html",
                    "formatted_body": self._markdown_to_html(message)
                }
            )
        except Exception as e:
            logger.error("Exception sending message to %s: %s", room.room_id, e, exc_info=True)
            return None

        if hasattr(response, 'event_id'):
            return response.event_id

        error_message = getattr(response, 'message', None) or getattr(response, 'error', None)
        status_code = getattr(response, 'status_code', None)
        logger.warning(
            "Failed to send message to %s. status=%s error=%s response=%s",
            room.room_id,
            status_code,
            error_message,
            response,
        )
        return None

    async def send_paginated_message(self, room: MatrixRoom, message: str, client: AsyncClient,
                                     user_id: str, current_page: int, total_pages: int,
                                     callback: Callable) -> Optional[str]:
        """Send a message with pagination support."""
        event_id = await self.send_message(room, message, client)
        logger.info(f"Sent message with event_id: {event_id}")

        if event_id and total_pages > 1:
            logger.info(f"Registering pagination for event {event_id}: page {current_page}/{total_pages}, user {user_id}")
            # Register pagination
            self.pagination.register(event_id, room.room_id, user_id, current_page, total_pages, callback)
            logger.info(f"Pagination registered. Active paginations: {list(self.pagination.paginations.keys())}")

            # Add initial reaction arrows so users know they can click
            import asyncio
            await asyncio.sleep(0.1)  # Small delay to ensure message is processed

            logger.info(f"Adding ⬅️ reaction to {event_id}")
            await client.room_send(
                room_id=room.room_id,
                message_type="m.reaction",
                content={
                    "m.relates_to": {
                        "rel_type": "m.annotation",
                        "event_id": event_id,
                        "key": "⬅️"
                    }
                }
            )

            logger.info(f"Adding ➡️ reaction to {event_id}")
            await client.room_send(
                room_id=room.room_id,
                message_type="m.reaction",
                content={
                    "m.relates_to": {
                        "rel_type": "m.annotation",
                        "event_id": event_id,
                        "key": "➡️"
                    }
                }
            )
            logger.info(f"Initial reactions added to {event_id}")
        else:
            logger.info(f"Not adding pagination - event_id: {event_id}, total_pages: {total_pages}")

        return event_id

    async def edit_message(self, room: MatrixRoom, event_id: str, new_message: str, client: AsyncClient):
        """Edit an existing message and keep user reactions."""
        logger.info(f"Editing message {event_id}")

        # Simply edit the message - Matrix will preserve user reactions
        await client.room_send(
            room_id=room.room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": f"* {new_message}",
                "format": "org.matrix.custom.html",
                "formatted_body": f"* {self._markdown_to_html(new_message)}",
                "m.new_content": {
                    "msgtype": "m.text",
                    "body": new_message,
                    "format": "org.matrix.custom.html",
                    "formatted_body": self._markdown_to_html(new_message)
                },
                "m.relates_to": {
                    "rel_type": "m.replace",
                    "event_id": event_id
                }
            }
        )
        logger.info(f"Message edited successfully")

    async def send_image_message(self, room: MatrixRoom, image_url: str, artist_name: str, client: AsyncClient):
        """Download image from Last.fm and upload to Matrix, then send."""
        try:
            # Download the image
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    if resp.status != 200:
                        logger.error(f"Failed to download image: HTTP {resp.status}")
                        return

                    image_data = await resp.read()
                    content_type = resp.headers.get('Content-Type', 'image/png')

                    logger.info(f"Downloaded image: {len(image_data)} bytes, Content-Type: {content_type}")
                    logger.info(f"First 50 bytes: {image_data[:50]}")

                    # Verify we got actual image data
                    if len(image_data) < 100:
                        logger.error(f"Image data too small ({len(image_data)} bytes), likely not a valid image")
                        return

                    # Check for PNG or JPEG magic bytes
                    if not (image_data[:8] == b'\x89PNG\r\n\x1a\n' or image_data[:2] == b'\xff\xd8'):
                        logger.error(f"Invalid image format. Magic bytes: {image_data[:10]}")
                        return

            # Wrap bytes in BytesIO for nio upload
            image_file = BytesIO(image_data)
            image_file.seek(0)  # Ensure we're at the start
            file_size = len(image_data)

            logger.info(f"Image validated: {file_size} bytes, type: {content_type}")

            # Upload to Matrix
            upload_response, _ = await client.upload(
                image_file,
                content_type=content_type,
                filename=f"{artist_name}.png",
                filesize=file_size
            )

            logger.info(f"Upload response type: {type(upload_response)}, response: {upload_response}")

            if isinstance(upload_response, UploadError):
                logger.error(f"Failed to upload image: {upload_response.message}")
                return

            if not isinstance(upload_response, UploadResponse) or not upload_response.content_uri:
                logger.error(f"Failed to upload image to Matrix. Response: {upload_response}")
                return

            # Send the image message with mxc:// URI
            await client.room_send(
                room_id=room.room_id,
                message_type="m.room.message",
                content={
                    "msgtype": "m.image",
                    "url": upload_response.content_uri,
                    "body": f"{artist_name}.png",
                    "info": {
                        "mimetype": content_type,
                    }
                }
            )
            logger.info(f"Successfully uploaded and sent image: {upload_response.content_uri}")

        except Exception as e:
            logger.error(f"Error sending image: {e}", exc_info=True)

    @staticmethod
    def _markdown_to_html(text: str) -> str:
        """Convert basic markdown to HTML."""
        # Links - must be before bold/italic to avoid conflicts
        text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
        # Bold
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        # Italic
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        # Code
        text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
        # Newlines
        text = text.replace('\n', '<br/>')
        return text

    # Discogs Commands

    async def show_discogs_help(self, room: MatrixRoom, client: AsyncClient):
        """Show Discogs help message."""
        help_text = f"""
**Discogs Commands:**

`{self.config.command_prefix}discogs link <username>` (dg link) - Link Discogs account
`{self.config.command_prefix}discogs stats [username]` (dg stats) - Show collection/wantlist stats
`{self.config.command_prefix}discogs collection [username] [page]` (dg collection) - Show collection items
`{self.config.command_prefix}discogs wantlist [username] [page]` (dg wantlist) - Show wantlist items
`{self.config.command_prefix}discogs search <query>` (dg search) - Search Discogs database
`{self.config.command_prefix}discogs artist <name>` (dg artist) - Search for artist info
`{self.config.command_prefix}discogs release <name>` (dg release) - Search for release info
`{self.config.command_prefix}discogs help` (dg help) - Show this help

**Examples:**
`{self.config.command_prefix}dg link MyDiscogsUsername`
`{self.config.command_prefix}dg stats`
`{self.config.command_prefix}dg collection`
`{self.config.command_prefix}dg search Pink Floyd`
        """
        await self.send_message(room, help_text, client)

    async def link_discogs_user(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Link a Matrix user to a Discogs account."""
        if not args:
            await self.send_message(room, f"Usage: `{self.config.command_prefix}discogs link <username>`", client)
            return

        discogs_username = args[0]

        # Verify the user exists on Discogs
        user_profile = await self.discogs.get_user_profile(discogs_username)
        if not user_profile:
            await self.send_message(
                room,
                f"❌ Could not find Discogs user '{discogs_username}'. Please check the username.",
                client
            )
            return

        # Link in database
        success = await self.db.link_discogs_user(sender, discogs_username)
        if success:
            await self.send_message(
                room,
                f"✅ Successfully linked your Matrix account to Discogs user **{discogs_username}**!",
                client
            )
        else:
            await self.send_message(
                room,
                "❌ Failed to link Discogs account. Please try again.",
                client
            )

    async def show_discogs_stats(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Show Discogs collection and wantlist stats."""
        # Get target user
        if args and args[0]:
            discogs_username = args[0]
        else:
            discogs_username = await self.db.get_discogs_username(sender)
            if not discogs_username:
                await self.send_message(
                    room,
                    f"❌ You haven't linked a Discogs account. Use `{self.config.command_prefix}discogs link <username>`",
                    client
                )
                return

        # Get collection stats
        collection_stats = await self.discogs.get_user_collection_stats(discogs_username)
        wantlist_stats = await self.discogs.get_user_wantlist_stats(discogs_username)

        if not collection_stats and not wantlist_stats:
            await self.send_message(
                room,
                f"❌ Could not retrieve stats for Discogs user '{discogs_username}'.",
                client
            )
            return

        collection_count = collection_stats.get('total_items', 0) if collection_stats else 0
        wantlist_count = wantlist_stats.get('total_wants', 0) if wantlist_stats else 0

        stats_text = f"""
**Discogs Stats for {discogs_username}**

📀 **Collection**: {collection_count:,} items
🎯 **Wantlist**: {wantlist_count:,} items
        """

        await self.send_message(room, stats_text.strip(), client)

    async def show_discogs_collection(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Show user's Discogs collection."""
        # Get target user
        if args and args[0] and not args[0].isdigit():
            discogs_username = args[0]
            page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
        else:
            discogs_username = await self.db.get_discogs_username(sender)
            if not discogs_username:
                await self.send_message(
                    room,
                    f"❌ You haven't linked a Discogs account. Use `{self.config.command_prefix}discogs link <username>`",
                    client
                )
                return
            page = int(args[0]) if args and args[0].isdigit() else 1

        # Create callback for pagination
        async def get_collection_page(page_num: int) -> str:
            collection = await self.discogs.get_user_collection(discogs_username, page=page_num, per_page=10)

            if not collection or 'releases' not in collection:
                return f"❌ Could not retrieve collection for Discogs user '{discogs_username}'."

            releases = collection.get('releases', [])
            pagination_info = collection.get('pagination', {})
            total_items = pagination_info.get('items', 0)
            total_pages = pagination_info.get('pages', 0)

            if not releases:
                return f"📀 **{discogs_username}'s Collection** (Page {page_num}/{total_pages})\n\nNo items found."

            # Sort releases alphabetically by artist name
            releases_sorted = sorted(releases, key=lambda r: r.get('basic_information', {}).get('artists', [{}])[0].get('name', 'Unknown').lower())

            # Format emoji mapping
            format_emoji = {
                'Vinyl': '💿',
                'LP': '💿',
                'CD': '💽',
                'Cassette': '📼',
                'Box Set': '📦',
                'File': '💾',
                'All Media': '🎵'
            }

            # Format collection items
            items_text = []
            for release in releases_sorted[:10]:
                basic_info = release.get('basic_information', {})
                release_id = basic_info.get('id', '')
                title = basic_info.get('title', 'Unknown')
                artists = basic_info.get('artists', [])
                artist_name = artists[0].get('name', 'Unknown') if artists else 'Unknown'
                year = basic_info.get('year', 'N/A')
                # Replace 0 year with N/A
                year = year if year and year != 0 else 'N/A'
                formats = basic_info.get('formats', [])
                format_name = formats[0].get('name', 'Unknown') if formats else 'Unknown'

                # Get emoji for format
                format_display = format_emoji.get(format_name, '🎵')

                # Create Discogs URL
                discogs_url = f"https://www.discogs.com/release/{release_id}" if release_id else None

                # Format the line
                if discogs_url:
                    items_text.append(f"[{format_display} {artist_name} - {title}]({discogs_url}) ({year})")
                else:
                    items_text.append(f"{format_display} {artist_name} - {title} ({year})")

            collection_text = f"""
📀 **{discogs_username}'s Collection** (Page {page_num}/{total_pages})
Total: {total_items:,} items

{chr(10).join(items_text)}
            """
            return collection_text.strip()

        # Get initial collection to check total pages
        collection = await self.discogs.get_user_collection(discogs_username, page=page, per_page=10)

        if not collection or 'releases' not in collection:
            await self.send_message(
                room,
                f"❌ Could not retrieve collection for Discogs user '{discogs_username}'.",
                client
            )
            return

        total_pages = collection.get('pagination', {}).get('pages', 1)

        # Generate initial content
        initial_content = await get_collection_page(page)

        # Send with pagination if multiple pages
        await self.send_paginated_message(
            room, initial_content, client,
            user_id=sender,
            current_page=page,
            total_pages=total_pages,
            callback=get_collection_page
        )

    async def show_discogs_wantlist(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Show user's Discogs wantlist."""
        # Get target user
        if args and args[0] and not args[0].isdigit():
            discogs_username = args[0]
            page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
        else:
            discogs_username = await self.db.get_discogs_username(sender)
            if not discogs_username:
                await self.send_message(
                    room,
                    f"❌ You haven't linked a Discogs account. Use `{self.config.command_prefix}discogs link <username>`",
                    client
                )
                return
            page = int(args[0]) if args and args[0].isdigit() else 1

        # Create callback for pagination
        async def get_wantlist_page(page_num: int) -> str:
            wantlist = await self.discogs.get_user_wantlist(discogs_username, page=page_num, per_page=10)

            if not wantlist or 'wants' not in wantlist:
                return f"❌ Could not retrieve wantlist for Discogs user '{discogs_username}'."

            wants = wantlist.get('wants', [])
            pagination_info = wantlist.get('pagination', {})
            total_items = pagination_info.get('items', 0)
            total_pages = pagination_info.get('pages', 0)

            if not wants:
                return f"🎯 **{discogs_username}'s Wantlist** (Page {page_num}/{total_pages})\n\nNo items found."

            # Sort wants alphabetically by artist name
            wants_sorted = sorted(wants, key=lambda w: w.get('basic_information', {}).get('artists', [{}])[0].get('name', 'Unknown').lower())

            # Format emoji mapping
            format_emoji = {
                'Vinyl': '💿',
                'LP': '💿',
                'CD': '💽',
                'Cassette': '📼',
                'Box Set': '📦',
                'File': '💾',
                'All Media': '🎵'
            }

            # Format wantlist items
            items_text = []
            for want in wants_sorted[:10]:
                basic_info = want.get('basic_information', {})
                release_id = basic_info.get('id', '')
                title = basic_info.get('title', 'Unknown')
                artists = basic_info.get('artists', [])
                artist_name = artists[0].get('name', 'Unknown') if artists else 'Unknown'
                year = basic_info.get('year', 'N/A')
                # Replace 0 year with N/A
                year = year if year and year != 0 else 'N/A'
                formats = basic_info.get('formats', [])
                format_name = formats[0].get('name', 'Unknown') if formats else 'Unknown'

                # Get emoji for format
                format_display = format_emoji.get(format_name, '🎵')

                # Create Discogs URL
                discogs_url = f"https://www.discogs.com/release/{release_id}" if release_id else None

                # Format the line
                if discogs_url:
                    items_text.append(f"[{format_display} {artist_name} - {title}]({discogs_url}) ({year})")
                else:
                    items_text.append(f"{format_display} {artist_name} - {title} ({year})")

            wantlist_text = f"""
🎯 **{discogs_username}'s Wantlist** (Page {page_num}/{total_pages})
Total: {total_items:,} items

{chr(10).join(items_text)}
            """
            return wantlist_text.strip()

        # Get initial wantlist to check total pages
        wantlist = await self.discogs.get_user_wantlist(discogs_username, page=page, per_page=10)

        if not wantlist or 'wants' not in wantlist:
            await self.send_message(
                room,
                f"❌ Could not retrieve wantlist for Discogs user '{discogs_username}'.",
                client
            )
            return

        total_pages = wantlist.get('pagination', {}).get('pages', 1)

        # Generate initial content
        initial_content = await get_wantlist_page(page)

        # Send with pagination if multiple pages
        await self.send_paginated_message(
            room, initial_content, client,
            user_id=sender,
            current_page=page,
            total_pages=total_pages,
            callback=get_wantlist_page
        )

    async def search_discogs(self, room: MatrixRoom, args: list, client: AsyncClient):
        """Search Discogs database."""
        if not args:
            await self.send_message(
                room,
                f"Usage: `{self.config.command_prefix}discogs search <query>`",
                client
            )
            return

        query = ' '.join(args)
        results = await self.discogs.search(query, per_page=5)

        if not results or 'results' not in results:
            await self.send_message(
                room,
                f"❌ No results found for '{query}'.",
                client
            )
            return

        items = results.get('results', [])
        if not items:
            await self.send_message(
                room,
                f"❌ No results found for '{query}'.",
                client
            )
            return

        # Format search results
        items_text = []
        for item in items[:5]:
            title = item.get('title', 'Unknown')
            item_type = item.get('type', 'Unknown')
            year = item.get('year', '')
            year_str = f" ({year})" if year else ""
            resource_url = item.get('resource_url', '')

            # Extract ID from resource_url and create Discogs link
            if resource_url:
                # Resource URLs look like: https://api.discogs.com/releases/123 or https://api.discogs.com/artists/456
                discogs_id = resource_url.rstrip('/').split('/')[-1]
                discogs_link = f"https://www.discogs.com/{item_type.lower()}s/{discogs_id}" if item_type.lower() in ['release', 'artist', 'master'] else resource_url.replace('api.discogs.com', 'www.discogs.com')
                items_text.append(f"• [{item_type}] [{title}]({discogs_link}){year_str}")
            else:
                items_text.append(f"• [{item_type}] {title}{year_str}")

        search_text = f"""
🔍 **Discogs Search Results** for '{query}'

{chr(10).join(items_text)}
        """

        await self.send_message(room, search_text.strip(), client)

    async def show_discogs_artist(self, room: MatrixRoom, args: list, client: AsyncClient):
        """Show Discogs artist info."""
        if not args:
            await self.send_message(
                room,
                f"Usage: `{self.config.command_prefix}discogs artist <name>`",
                client
            )
            return

        artist_name = ' '.join(args)
        artists = await self.discogs.search_artist(artist_name, limit=1)

        if not artists:
            await self.send_message(
                room,
                f"❌ No artist found for '{artist_name}'.",
                client
            )
            return

        artist_id = artists[0].get('id')
        artist_info = await self.discogs.get_artist(artist_id)

        if not artist_info:
            await self.send_message(
                room,
                f"❌ Could not retrieve info for artist '{artist_name}'.",
                client
            )
            return

        name = artist_info.get('name', 'Unknown')
        real_name = artist_info.get('realname', '')
        profile = artist_info.get('profile', 'No profile available.')
        artist_id = artist_info.get('id', '')

        # Truncate profile if too long
        if len(profile) > 500:
            profile = profile[:500] + "..."

        # Create Discogs link
        discogs_link = f"https://www.discogs.com/artist/{artist_id}" if artist_id else ""
        link_text = f" - [{discogs_link}]({discogs_link})" if discogs_link else ""

        artist_text = f"""
🎵 **[{name}]({discogs_link})**{f" | {real_name}" if real_name else ""}

{profile}
        """

        await self.send_message(room, artist_text.strip(), client)

    async def show_discogs_release(self, room: MatrixRoom, args: list, client: AsyncClient):
        """Show Discogs release info."""
        if not args:
            await self.send_message(
                room,
                f"Usage: `{self.config.command_prefix}discogs release <name>`",
                client
            )
            return

        release_name = ' '.join(args)
        releases = await self.discogs.search_release(release_name, limit=1)

        if not releases:
            await self.send_message(
                room,
                f"❌ No release found for '{release_name}'.",
                client
            )
            return

        release_id = releases[0].get('id')
        release_info = await self.discogs.get_release(release_id)

        if not release_info:
            await self.send_message(
                room,
                f"❌ Could not retrieve info for release '{release_name}'.",
                client
            )
            return

        title = release_info.get('title', 'Unknown')
        artists = release_info.get('artists', [])
        artist_name = artists[0].get('name', 'Unknown') if artists else 'Unknown'
        year = release_info.get('year', 'N/A')
        genres = ', '.join(release_info.get('genres', []))
        styles = ', '.join(release_info.get('styles', []))

        # Create Discogs link
        discogs_link = f"https://www.discogs.com/release/{release_id}"

        tracklist = release_info.get('tracklist', [])
        tracks_text = []
        for track in tracklist[:10]:  # Show first 10 tracks
            position = track.get('position', '')
            track_title = track.get('title', 'Unknown')
            duration = track.get('duration', '')
            tracks_text.append(f"{position}. {track_title} {f'({duration})' if duration else ''}")

        release_text = f"""
💿 **[{artist_name} - {title}]({discogs_link})** ({year})

Genres: {genres if genres else 'N/A'}
Styles: {styles if styles else 'N/A'}

**Tracklist:**
{chr(10).join(tracks_text) if tracks_text else 'No tracklist available'}
        """

        await self.send_message(room, release_text.strip(), client)
