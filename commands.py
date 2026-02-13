"""
Command handler for bot commands
"""

import logging
import json
import re
import aiohttp
from io import BytesIO
from typing import Optional
from nio import AsyncClient, RoomMessage, MatrixRoom
from nio.responses import UploadResponse, UploadError

from database import Database
from lastfm_client import LastfmClient
from config import Config

logger = logging.getLogger(__name__)


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
        'lb': 'leaderboard',
        'r': 'recent',
        's': 'stats',
        'l': 'link',
        '?': 'help',
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

    def __init__(self, db: Database, lastfm: LastfmClient, config: Config):
        self.db = db
        self.lastfm = lastfm
        self.config = config

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
            parts = message[len(self.config.command_prefix):].split()
            if not parts:
                return

            command = self.normalize_command(parts[0].lower())
            args = parts[1:]

            # Route to appropriate handler
            if command == 'help':
                await self.show_help(room, client)
            elif command == 'lastfm':
                if not args:
                    await self.show_help(room, client)
                elif self.normalize_command(args[0]) == 'help':
                    await self.show_help(room, client)
                elif self.normalize_command(args[0]) == 'link':
                    await self.link_user(room, sender, args[1:], client)
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
                elif self.normalize_command(args[0]) == 'whoknows':
                    await self.who_knows(room, args[1:], client)
                elif self.normalize_command(args[0]) == 'whoknowstrack':
                    await self.who_knows_track(room, args[1:], client)
                elif self.normalize_command(args[0]) == 'whoknowsalbum':
                    await self.who_knows_album(room, args[1:], client)
                elif self.normalize_command(args[0]) == 'leaderboard':
                    await self.show_leaderboard(room, args[1:], client)
                else:
                    await self.send_message(room, f"Unknown command: {args[0]}", client)
            else:
                await self.send_message(room, f"Unknown command. Type `{self.config.command_prefix}fm help` for help.", client)

        except Exception as e:
            logger.error(f"Error handling command: {e}", exc_info=True)
            await self.send_message(room, f"Error processing command: {str(e)}", client)

    async def show_help(self, room: MatrixRoom, client: AsyncClient):
        """Show help message."""
        help_text = f"""
FMatrix Bot - Last.fm Stats & Leaderboards

**Commands:**
`{self.config.command_prefix}fm link <username>` (l) - Link your Last.fm account
`{self.config.command_prefix}fm stats` (s) - Show listening stats
`{self.config.command_prefix}fm topartists [period]` (tar) - Show top artists
`{self.config.command_prefix}fm topalbums [period]` (ta/tb) - Show top albums
`{self.config.command_prefix}fm toptracks [period]` (tt) - Show top tracks
`{self.config.command_prefix}fm recent` (r) - Show recent tracks
`{self.config.command_prefix}fm whoknows <artist>` (wk) - Who in this room knows this artist
`{self.config.command_prefix}fm whoknowstrack <track>` (wkt) - Who in this room knows this track
`{self.config.command_prefix}fm whoknowsalbum <album>` (wka) - Who in this room knows this album
`{self.config.command_prefix}fm leaderboard [stat]` (lb) - Show room leaderboard
`{self.config.command_prefix}fm help` (?) - Show this help

**Period Abbreviations:**
7days/7day/7d, 1month/1m, 3month/3m, 6month/6m, 12month/12m, overall/1y/y/all

**Period Options:**
overall/1y/y/all, 12month/12m, 6month/6m, 3month/3m, 1month/1m, 7days/7day/7d

**Leaderboard Types:**
playcounts (default), artistcount, trackcount

**Examples:**
`{self.config.command_prefix}fm ta 7d` - Top albums last 7 days
`{self.config.command_prefix}fm tt 1m` - Top tracks last month
`{self.config.command_prefix}fm tar` - Top artists all time
`{self.config.command_prefix}fm lb` - Leaderboard by scrobbles
`{self.config.command_prefix}fm wk Beyoncé` - Who knows Beyoncé
        """
        await self.send_message(room, help_text, client)

    async def link_user(self, room: MatrixRoom, sender: str, args: list, client: AsyncClient):
        """Link a Matrix user to a Last.fm account."""
        if not args:
            await self.send_message(room, f"Usage: `{self.config.command_prefix}fm link <username>`", client)
            return

        lastfm_username = args[0]

        # Verify the username exists on Last.fm
        user_info = await self.lastfm.get_user_info(lastfm_username)
        if not user_info:
            await self.send_message(room, f"❌ Last.fm user '{lastfm_username}' not found", client)
            return

        # Store the mapping
        success = await self.db.link_user(sender, lastfm_username)
        if success:
            await self.send_message(
                room,
                f"✅ Linked {sender} to Last.fm user **{lastfm_username}**",
                client
            )
        else:
            await self.send_message(room, "❌ Failed to link account", client)

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
                top_artists = await self.lastfm.get_top_artists(lastfm_user, period='overall', limit=100)
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
                top_tracks = await self.lastfm.get_top_tracks(lastfm_user, period='overall', limit=1000)
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
                top_albums = await self.lastfm.get_top_albums(lastfm_user, period='overall', limit=1000)
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

    async def send_message(self, room: MatrixRoom, message: str, client: AsyncClient):
        """Send a message to the room."""
        await client.room_send(
            room_id=room.room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": message,
                "format": "org.matrix.custom.html",
                "formatted_body": self._markdown_to_html(message)
            }
        )

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
        # Bold
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        # Italic
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        # Code
        text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
        # Newlines
        text = text.replace('\n', '<br/>')
        return text
