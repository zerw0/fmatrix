from __future__ import annotations

import logging
import re
import time
from io import BytesIO
from typing import Callable, Dict, Optional

import aiohttp
from nio import AsyncClient, MatrixRoom
from nio.responses import UploadError, UploadResponse
from PIL import Image

logger = logging.getLogger(__name__)


class LastfmCommandsMixin:
    async def show_help(self, room: MatrixRoom, client: AsyncClient):
        """Show help message."""
        discogs_info = ""
        if self.discogs:
            discogs_info = f"\n\n**Discogs Integration:**\nUse `{self.config.command_prefix}discogs help` (dg help) for Discogs commands"

        spotify_info = ""
        if self.spotify:
            spotify_info = (
                f"\n\n**Spotify Commands:**\n"
                f"`{self.config.command_prefix}spotify` (sp) - Get Spotify link for your now playing track\n"
                f"`{self.config.command_prefix}spotify <artist> - <track>` (sp) - Search and get Spotify link for a specific track\n"
                f"`{self.config.command_prefix}fm spotify` - Get Spotify link from within the fm command"
            )

        lyrics_info = (
            f"\n\n**Lyrics Commands:**\n"
            f"`{self.config.command_prefix}lyrics` (ly) - Show lyrics for your now playing track\n"
            f"`{self.config.command_prefix}lyrics <artist> - <track>` (ly) - Show lyrics for a specific track\n"
            f"`{self.config.command_prefix}fm lyrics` - Show lyrics from within the fm command"
        )

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
`{self.config.command_prefix}fm whoknows [artist]` (wk) - Who in this room knows this artist (defaults to your current artist)
`{self.config.command_prefix}fm whoknowstrack [track]` (wkt) - Who in this room knows this track (defaults to your current track)
`{self.config.command_prefix}fm whoknowsalbum [album]` (wka) - Who in this room knows this album (defaults to your current album)
`{self.config.command_prefix}fm chart [size] [period] [flags]` (c) - Generate album collage
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

**GitHub:** [Source Code](https://github.com/zerw0/fmatrix){discogs_info}{spotify_info}{lyrics_info}
        """
        await self.send_message(room, help_text, client)

    async def link_user(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
        """Link a Matrix user to a Last.fm account and start auth flow."""
        logger.info(
            f"link_user called - sender type: {type(sender).__name__}, sender value: '{sender}'"
        )
        if not args:
            await self.send_message(
                room, f"Usage: `{self.config.command_prefix}fm link <username>`", client
            )
            return

        lastfm_username = args[0]

        # Verify the username exists on Last.fm
        logger.info(f"Linking {sender} to Last.fm user {lastfm_username}")
        user_info = await self.lastfm.get_user_info(lastfm_username)
        if not user_info:
            await self.send_message(
                room, f"❌ Last.fm user '{lastfm_username}' not found", client
            )
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
            f"✅ Linked {sender} to Last.fm user **{lastfm_username}**\n\nSending authorization link via DM...",
            client,
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
                    client,
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
                user_id = (
                    sender
                    if sender.startswith("@")
                    else f"@{sender}:{client.homeserver.split('https://')[-1]}"
                )
                logger.info(
                    f"Attempting to send DM to {sender} with full ID: {user_id}"
                )

                # Create DM room
                from nio.responses import RoomCreateResponse

                dm_response = await client.room_create(is_direct=True, invite=[user_id])

                logger.info(
                    f"DM room creation response type: {type(dm_response).__name__}"
                )

                if isinstance(dm_response, RoomCreateResponse):
                    dm_room_id = dm_response.room_id
                    logger.info(f"Created DM room: {dm_room_id}")

                    # Send the message to the DM
                    send_response = await client.room_send(
                        dm_room_id,
                        "m.room.message",
                        {"msgtype": "m.text", "body": message},
                    )
                    logger.info(
                        f"DM message send response type: {type(send_response).__name__}"
                    )
                    sent_dm = True
                    await self.send_message(
                        room, "✅ Authorization link sent via DM!", client
                    )
                else:
                    logger.warning(
                        f"Unexpected response type from room_create: {type(dm_response).__name__} - {dm_response}"
                    )
                    await self.send_message(
                        room, f"⚠️ Could not send DM. Showing auth link here:", client
                    )
                    await self.send_message(room, message, client)
            except Exception as dm_error:
                logger.error(f"Failed to send DM: {dm_error}", exc_info=True)
                await self.send_message(
                    room, f"⚠️ Could not send DM. Showing auth link here:", client
                )
                await self.send_message(room, message, client)

        except Exception as e:
            logger.error(f"Error in auth flow: {e}", exc_info=True)
            await self.send_message(room, f"❌ Error: {str(e)}", client)

    async def set_session_key(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
        """Set Last.fm session key for authenticated commands."""
        if not args:
            await self.send_message(
                room,
                f"Usage: `{self.config.command_prefix}fm sessionkey <your_session_key>`\n\nGet your session key from: https://www.last.fm/api/auth",
                client,
            )
            return

        session_key = args[0]

        # Store the session key
        success = await self.db.set_lastfm_session_key(sender, session_key)
        if success:
            await self.send_message(
                room,
                f"✅ Session key saved. You can now use love/unlove commands!",
                client,
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
                    client,
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

    async def complete_auth_flow(
        self, room: MatrixRoom, sender: str, client: AsyncClient
    ):
        """Complete Last.fm authorization by exchanging token for session key."""
        try:
            logger.info(
                f"complete_auth_flow called - sender type: {type(sender).__name__}, sender value: '{sender}'"
            )

            # Get the stored auth token
            auth_token = await self.db.get_auth_token(sender)
            if not auth_token:
                logger.warning(f"No auth token found for {sender}")
                await self.send_message(
                    room,
                    f"❌ No pending auth token. Run `{self.config.command_prefix}fm link` first.",
                    client,
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
                    client,
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
                client,
            )

        except Exception as e:
            logger.error(f"Error completing auth: {e}", exc_info=True)
            await self.send_message(room, f"❌ Error: {str(e)}", client)

    async def show_stats(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
        """Show user's Last.fm stats."""
        target_user = await self._get_target_user(room, sender, client, args)
        if not target_user:
            return

        # Get stats
        stats = await self.lastfm.get_user_stats(target_user)
        if not stats:
            await self.send_message(
                room, f"❌ Could not fetch stats for {target_user}", client
            )
            return

        # Format message
        message = f"""
**{stats["username"]}**'s Last.fm Stats:
📊 Scrobbles: {stats["play_count"]:,}
🎤 Artists: {stats["artist_count"]:,}
🎵 Tracks: {stats["track_count"]:,}
💿 Albums: {stats["album_count"]:,}
        """

        await self.send_message(room, message, client)

    async def show_top_artists(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
        """Show user's top artists."""
        period = self.normalize_period(args[0].lower()) if args else "overall"

        if not await self._validate_period(room, period, client):
            return

        target_user = await self._get_target_user(room, sender, client)
        if not target_user:
            return

        artists = await self.lastfm.get_top_artists(target_user, period, limit=10)
        if not artists:
            await self.send_message(
                room, f"❌ Could not fetch top artists for {target_user}", client
            )
            return

        period_name = self._get_period_name(period)
        message = f"**Top Artists ({period_name})**\n\n"

        for i, artist in enumerate(artists, 1):
            name = artist.get("name", "Unknown")
            plays = artist.get("playcount", "0")
            message += f"{i}. {name} - {plays} plays\n"

        await self.send_message(room, message, client)

    async def show_top_albums(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
        """Show user's top albums."""
        period = self.normalize_period(args[0].lower()) if args else "overall"

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
            name = album.get("name", "Unknown")
            artist_name = self._extract_artist_name(album.get("artist", {}))
            plays = album.get("playcount", "0")
            message += f"{i}. {name} by {artist_name} - {plays} plays\n"

        await self.send_message(room, message, client)

    async def show_top_tracks(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
        """Show user's top tracks."""
        period = self.normalize_period(args[0].lower()) if args else "overall"

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
            name = track.get("name", "Unknown")
            artist_name = self._extract_artist_name(track.get("artist", {}))
            plays = track.get("playcount", "0")
            message += f"{i}. {name} by {artist_name} - {plays} plays\n"

        await self.send_message(room, message, client)

    async def show_recent_tracks(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
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
            name = track.get("name", "Unknown")
            artist_name = self._extract_artist_name(track.get("artist", {}))
            message += f"{i}. {name} by {artist_name}\n"

        await self.send_message(room, message, client)

    async def show_now_playing(
        self, room: MatrixRoom, sender: str, client: AsyncClient
    ):
        """Show user's currently playing track."""
        target_user = await self._get_target_user(room, sender, client)
        if not target_user:
            return

        cached_entry = self._now_playing_cache.get(target_user)
        if cached_entry:
            age = time.monotonic() - cached_entry["timestamp"]
            if age < self._now_playing_ttl_seconds:
                await self.send_message(room, cached_entry["message"], client)
                return

        track = await self.lastfm.get_now_playing(target_user)
        if not track:
            await self.send_message(
                room, f"❌ Could not fetch now playing track for {target_user}", client
            )
            return

        name = track.get("name", "Unknown")
        artist_name = self._extract_artist_name(track.get("artist", {}))
        album = track.get("album", {})
        album_name = (
            album.get("text", "Unknown")
            if isinstance(album, dict)
            else album or "Unknown"
        )
        track_cache_key = self._normalize_cache_text(name) or name
        artist_cache_key = self._normalize_cache_text(artist_name) or artist_name
        play_count = track.get("userplaycount")
        if play_count is None:
            cached_playcount = await self.db.get_cached_playcount(
                target_user,
                "track",
                track_cache_key,
                artist_name=artist_cache_key,
                max_age_hours=1,
            )
            if cached_playcount is not None:
                play_count = cached_playcount
            else:
                track_info = await self.lastfm.get_track_info(
                    artist_name, name, username=target_user
                )
                if track_info:
                    user_playcount = track_info.get("userplaycount")
                    if user_playcount is not None:
                        play_count = int(user_playcount) if user_playcount else 0
                        await self.db.cache_playcount(
                            target_user,
                            "track",
                            track_cache_key,
                            play_count,
                            artist_name=artist_cache_key,
                        )

        if play_count is None:
            play_count = "N/A"

        message = f"🎵 **Now Playing - {target_user}**\n\n"
        message += f"**{name}**\n"
        message += f"by *{artist_name}*\n"
        message += f"on {album_name}\n"
        message += f"Plays: {play_count}"

        self._now_playing_cache[target_user] = {
            "timestamp": time.monotonic(),
            "message": message,
        }
        await self.send_message(room, message, client)

    async def show_spotify_link(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
        """Show Spotify link for a track.

        If no args: Get current track from Last.fm and search on Spotify
        If args provided: Search for the provided song name on Spotify
        """
        track_name = None
        artist_name = None

        if not args:
            # Get current track from Last.fm
            target_user = await self._get_target_user(room, sender, client)
            if not target_user:
                return

            track = await self.lastfm.get_now_playing(target_user)
            if not track:
                await self.send_message(
                    room,
                    f"❌ Could not fetch now playing track for {target_user}",
                    client,
                )
                return

            track_name = track.get("name", "Unknown")
            artist_name = self._extract_artist_name(track.get("artist", {}))
        else:
            # Parse the provided args as song name or "artist - track"
            args_str = " ".join(args)
            if " - " in args_str:
                parts = args_str.split(" - ", 1)
                artist_name = parts[0].strip()
                track_name = parts[1].strip()
            else:
                track_name = args_str

        # Search on Spotify
        if not track_name:
            await self.send_message(room, "❌ Could not determine track name", client)
            return

        # Build search query
        if artist_name:
            search_query = f"{artist_name} {track_name}"
        else:
            search_query = track_name

        # Search Spotify
        spotify_results = await self.spotify.search_track(search_query, limit=10)
        if not spotify_results:
            await self.send_message(
                room, f"❌ Could not find '{search_query}' on Spotify", client
            )
            return

        # Use fuzzy matching to select the best result
        best_track = self._select_best_spotify_result(spotify_results, search_query)
        if not best_track or not best_track.get("external_urls", {}).get("spotify"):
            await self.send_message(
                room, f"❌ Could not find '{search_query}' on Spotify", client
            )
            return

        # Format the result
        spotify_track = self._format_spotify_track(best_track)

        # Format message
        message = f"🎵 **Spotify Link**\n\n"
        message += f"**{spotify_track['name']}**\n"
        message += f"by *{spotify_track['artist']}*\n"
        if spotify_track.get("album"):
            message += f"on {spotify_track['album']}\n"
        message += f"\n🔗 [Open on Spotify]({spotify_track['url']})"

        await self.send_message(room, message, client)

    async def show_lyrics(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
        """Show lyrics for a track.

        If no args: Get current track from Last.fm and fetch lyrics
        If args provided: Search for the provided song's lyrics
        """
        track_name = None
        artist_name = None

        if not args:
            # Get current track from Last.fm
            target_user = await self._get_target_user(room, sender, client)
            if not target_user:
                return

            track = await self.lastfm.get_now_playing(target_user)
            if not track:
                await self.send_message(
                    room,
                    f"❌ Could not fetch now playing track for {target_user}",
                    client,
                )
                return

            track_name = track.get("name", "Unknown")
            artist_name = self._extract_artist_name(track.get("artist", {}))
        else:
            # Parse the provided args as "artist - track" or just a query
            args_str = " ".join(args)
            if " - " in args_str:
                parts = args_str.split(" - ", 1)
                artist_name = parts[0].strip()
                track_name = parts[1].strip()
            else:
                # Try to search Last.fm first to resolve artist/track
                candidates = await self.lastfm.search_track(args_str, limit=5)
                best = self._select_best_lastfm_result(candidates, args_str, "track")
                if best:
                    track_name = best.get("name", args_str)
                    artist_name = self._extract_artist_name(best.get("artist", {}))
                else:
                    # Fall back to using the raw query as track name
                    track_name = args_str

        if not track_name:
            await self.send_message(room, "❌ Could not determine track name.", client)
            return

        # Fetch lyrics
        result = await self.lyrics.get_lyrics(
            artist=artist_name or "",
            track=track_name,
        )

        if not result or not result.get("plain"):
            query_display = (
                f"**{track_name}** by *{artist_name}*"
                if artist_name
                else f"**{track_name}**"
            )
            await self.send_message(
                room,
                f"❌ No lyrics found for {query_display}",
                client,
            )
            return

        # Format the lyrics message
        lyrics_artist = result.get("artist") or artist_name or "Unknown"
        lyrics_track = result.get("track") or track_name or "Unknown"
        lyrics_album = result.get("album", "")
        plain_lyrics = result["plain"]

        # Truncate if too long for a single message (Matrix has limits)
        max_len = 4000
        truncated = False
        if len(plain_lyrics) > max_len:
            plain_lyrics = plain_lyrics[:max_len].rsplit("\n", 1)[0]
            truncated = True

        message = f"📝 **Lyrics: {lyrics_track}**\n"
        message += f"by *{lyrics_artist}*\n"
        if lyrics_album:
            message += f"on {lyrics_album}\n"
        message += f"\n{plain_lyrics}"
        if truncated:
            message += "\n\n*(lyrics truncated — too long for a single message)*"
        message += "\n\n*Powered by [lrclib.net](https://lrclib.net)*"

        await self.send_message(room, message, client)

    async def show_track_info(self, room: MatrixRoom, args: list, client: AsyncClient):
        """Show track information including playcount."""
        if len(args) < 2:
            await self.send_message(
                room,
                f"❌ Usage: {self.config.command_prefix}track <artist> - <track>",
                client,
            )
            return

        # Find the dash separator
        try:
            dash_idx = args.index("-")
            artist_name = " ".join(args[:dash_idx])
            track_name = " ".join(args[dash_idx + 1 :])
        except ValueError:
            await self.send_message(
                room,
                f"❌ Usage: {self.config.command_prefix}track <artist> - <track>",
                client,
            )
            return

        track_info = await self.lastfm.get_track_info(artist_name, track_name)
        resolved_artist = artist_name
        resolved_track = track_name

        if not track_info:
            search_query = f"{artist_name} {track_name}".strip()
            candidates = await self.lastfm.search_track(search_query, limit=10)
            best_match = self._select_best_lastfm_result(
                candidates, search_query, "track"
            )
            if best_match:
                resolved_track = best_match.get("name", track_name)
                resolved_artist = self._extract_artist_name(
                    best_match.get("artist", {})
                )
                track_info = await self.lastfm.get_track_info(
                    resolved_artist, resolved_track
                )

        if not track_info:
            await self.send_message(
                room, f"❌ Could not find track: {track_name} by {artist_name}", client
            )
            return

        name = track_info.get("name", "Unknown")
        artist = track_info.get("artist", {})
        artist_name = (
            artist.get("name", "Unknown")
            if isinstance(artist, dict)
            else artist or "Unknown"
        )
        listeners = track_info.get("listeners", "N/A")
        plays = track_info.get("playcount", "N/A")
        tags = track_info.get("toptags", {})
        tag_list = tags.get("tag", []) if isinstance(tags, dict) else []
        tag_str = (
            ", ".join([t.get("name", "") for t in tag_list[:5]])
            if tag_list
            else "No tags"
        )

        message = f"🎵 **Track Info: {name}**\n\n"
        if resolved_artist != artist_name or resolved_track != track_name:
            message += f"Closest match: {resolved_track} by {resolved_artist}\n"
        message += f"**Artist:** {artist_name}\n"
        message += f"**Listeners:** {listeners}\n"
        message += f"**Total Plays:** {plays}\n"
        message += f"**Tags:** {tag_str}"

        await self.send_message(room, message, client)

    async def show_loved_tracks(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
        """Show user's loved tracks."""
        target_user = await self._get_target_user(room, sender, client, args)
        if not target_user:
            return

        limit = 10
        if args and args[0].isdigit():
            limit = min(int(args[0]), 50)

        tracks = await self.lastfm.get_user_loved_tracks(target_user, limit=limit)
        if not tracks:
            await self.send_message(
                room, f"❌ Could not fetch loved tracks for {target_user}", client
            )
            return

        message = f"❤️ **Loved Tracks - {target_user}**\n\n"

        for i, track in enumerate(tracks, 1):
            name = track.get("name", "Unknown")
            artist_name = self._extract_artist_name(track.get("artist", {}))
            message += f"{i}. {name} by {artist_name}\n"

        await self.send_message(room, message, client)

    async def love_track_command(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
        """Love a track (requires session key)."""
        logger.info(f"love_track_command called with args: {args}")
        if len(args) < 1:
            await self.send_message(
                room,
                f"❌ Usage: {self.config.command_prefix}fm love <artist> - <track> or {self.config.command_prefix}fm love <song>",
                client,
            )
            return

        artist_name = ""
        track_name = ""
        if "-" in args:
            try:
                dash_idx = args.index("-")
                artist_name = " ".join(args[:dash_idx])
                track_name = " ".join(args[dash_idx + 1 :])
                logger.info(f"Parsed: artist='{artist_name}', track='{track_name}'")
            except ValueError:
                await self.send_message(
                    room,
                    f"❌ Usage: {self.config.command_prefix}fm love <artist> - <track> or {self.config.command_prefix}fm love <song>",
                    client,
                )
                return
        else:
            query = " ".join(args).strip()
            if not query:
                await self.send_message(
                    room,
                    f"❌ Usage: {self.config.command_prefix}fm love <artist> - <track> or {self.config.command_prefix}fm love <song>",
                    client,
                )
                return
            candidates = await self.lastfm.search_track(query, limit=10)
            best_match = self._select_best_lastfm_result(candidates, query, "track")
            if not best_match:
                await self.send_message(
                    room, f"❌ Could not find a track matching '{query}'", client
                )
                return
            track_name = best_match.get("name", query)
            artist_name = self._extract_artist_name(best_match.get("artist", {}))
            logger.info(
                f"Resolved from query: artist='{artist_name}', track='{track_name}'"
            )

        # Get session key
        session_key = await self.db.get_lastfm_session_key(sender)
        logger.info(f"Got session key for {sender}: {bool(session_key)}")
        if not session_key:
            await self.send_message(
                room,
                f"❌ You don't have a Last.fm session key. This feature requires Last.fm authentication.",
                client,
            )
            return

        # Love the track
        logger.info(
            f"Calling love_track with artist='{artist_name}', track='{track_name}', session_key=***"
        )
        success = await self.lastfm.love_track(artist_name, track_name, session_key)
        resolved_artist = artist_name
        resolved_track = track_name

        if not success:
            search_query = f"{artist_name} {track_name}".strip()
            candidates = await self.lastfm.search_track(search_query, limit=10)
            best_match = self._select_best_lastfm_result(
                candidates, search_query, "track"
            )
            if best_match:
                resolved_track = best_match.get("name", track_name)
                resolved_artist = self._extract_artist_name(
                    best_match.get("artist", {})
                )
                if resolved_artist != artist_name or resolved_track != track_name:
                    logger.info(
                        f"Retrying love_track with closest match artist='{resolved_artist}', track='{resolved_track}'"
                    )
                    success = await self.lastfm.love_track(
                        resolved_artist, resolved_track, session_key
                    )

        if success:
            if resolved_artist != artist_name or resolved_track != track_name:
                await self.send_message(
                    room,
                    f"❤️ Loved closest match: **{resolved_track}** by {resolved_artist}",
                    client,
                )
            else:
                await self.send_message(
                    room, f"❤️ Loved: **{track_name}** by {artist_name}", client
                )
        else:
            await self.send_message(
                room, f"❌ Failed to love track: {track_name} by {artist_name}", client
            )

    async def unlove_track_command(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
        """Unlove a track (requires session key)."""
        logger.info(f"unlove_track_command called with args: {args}")
        if len(args) < 2:
            await self.send_message(
                room,
                f"❌ Usage: {self.config.command_prefix}fm unlove <artist> - <track>",
                client,
            )
            return

        # Find the dash separator
        try:
            dash_idx = args.index("-")
            artist_name = " ".join(args[:dash_idx])
            track_name = " ".join(args[dash_idx + 1 :])
            logger.info(f"Parsed unlove: artist='{artist_name}', track='{track_name}'")
        except ValueError:
            await self.send_message(
                room,
                f"❌ Usage: {self.config.command_prefix}fm unlove <artist> - <track>",
                client,
            )
            return

        # Get session key
        session_key = await self.db.get_lastfm_session_key(sender)
        logger.info(f"Got session key for {sender}: {bool(session_key)}")
        if not session_key:
            await self.send_message(
                room,
                f"❌ You don't have a Last.fm session key. This feature requires Last.fm authentication.",
                client,
            )
            return

        # Unlove the track
        logger.info(
            f"Calling unlove_track with artist='{artist_name}', track='{track_name}', session_key=***"
        )
        success = await self.lastfm.unlove_track(artist_name, track_name, session_key)
        if success:
            await self.send_message(
                room, f"💔 Unloved: **{track_name}** by {artist_name}", client
            )
        else:
            await self.send_message(
                room,
                f"❌ Failed to unlove track: {track_name} by {artist_name}",
                client,
            )

    async def show_leaderboard(self, room: MatrixRoom, args: list, client: AsyncClient):
        """Show leaderboard of room members' Last.fm stats."""
        stat_type = args[0] if args else "playcounts"

        # Get all room members
        room_members = list(room.users.keys())
        if not room_members:
            await self.send_message(room, "❌ No members in room", client)
            return

        # Get Last.fm usernames for all members
        user_mapping = await self.db.get_all_users_in_room(room.room_id, room_members)
        if not user_mapping:
            await self.send_message(
                room, "❌ No one in this room has linked a Last.fm account", client
            )
            return

        # Fetch stats
        leaderboard_data = []
        for matrix_user, lastfm_user in user_mapping.items():
            stats = await self.lastfm.get_user_stats(lastfm_user)
            if stats:
                leaderboard_data.append({"lastfm": lastfm_user, "stats": stats})

        if not leaderboard_data:
            await self.send_message(room, "❌ Could not fetch stats", client)
            return

        # Sort by stat type
        if stat_type == "playcounts":
            leaderboard_data.sort(key=lambda x: x["stats"]["play_count"], reverse=True)
            stat_display = "Scrobbles"
            stat_key = "play_count"
        elif stat_type == "artistcount":
            leaderboard_data.sort(
                key=lambda x: x["stats"]["artist_count"], reverse=True
            )
            stat_display = "Artists"
            stat_key = "artist_count"
        elif stat_type == "trackcount":
            leaderboard_data.sort(key=lambda x: x["stats"]["track_count"], reverse=True)
            stat_display = "Tracks"
            stat_key = "track_count"
        else:
            await self.send_message(
                room,
                f"❌ Unknown stat type. Use: playcounts, artistcount, trackcount",
                client,
            )
            return

        # Build message
        message = f"**🏆 Room Leaderboard - {stat_display}**\n\n"
        medals = ["🥇", "🥈", "🥉"]

        for i, entry in enumerate(leaderboard_data[:10], 1):
            medal = medals[i - 1] if i <= 3 else f"{i}."
            username = entry["lastfm"]
            stat_value = entry["stats"][stat_key]
            message += f"{medal} {username}: {stat_value:,}\n"

        await self.send_message(room, message, client)

    async def _get_now_playing_context(
        self, room: MatrixRoom, sender: str, client: AsyncClient
    ) -> Optional[Dict[str, str]]:
        target_user = await self._get_target_user(room, sender, client)
        if not target_user:
            return None

        track = await self.lastfm.get_now_playing(target_user)
        if not track:
            await self.send_message(
                room, f"❌ Could not fetch now playing track for {target_user}", client
            )
            return None

        name = track.get("name", "")
        artist_name = self._extract_artist_name(track.get("artist", {}))
        album = track.get("album", {})
        if isinstance(album, dict):
            album_name = album.get("#text") or album.get("text") or ""
        else:
            album_name = album or ""

        return {
            "track": name,
            "artist": artist_name,
            "album": album_name,
        }

    async def who_knows(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
        """Show who in the room listens to this artist."""
        if not args:
            now_playing = await self._get_now_playing_context(room, sender, client)
            if not now_playing:
                return
            artist_name = now_playing["artist"]
            if not artist_name:
                await self.send_message(
                    room, "❌ No artist found for your current track", client
                )
                return
        else:
            artist_name = " ".join(args)
        artists = await self.lastfm.search_artist(artist_name, limit=10)

        if not artists:
            await self.send_message(
                room, f"❌ No artists found matching '{artist_name}'", client
            )
            return

        # Pick closest + most popular result
        top_artist = (
            self._select_best_lastfm_result(artists, artist_name, "artist")
            or artists[0]
        )
        artist_name_clean = top_artist.get("name", artist_name)

        # Try to get image from search results first (as fallback)
        search_image = None
        search_image_list = top_artist.get("image", [])
        placeholder_hashes = ["2a96cbd8b46e442fc41c2b86b821562f"]

        if isinstance(search_image_list, list) and len(search_image_list) > 0:
            # Get 'large' size (174s) instead of extralarge for more compact display
            for img in search_image_list:
                if img.get("size") == "large":
                    img_url = img.get("#text", "").strip()
                    if img_url and "/noimage" not in img_url.lower():
                        is_placeholder = any(
                            placeholder_hash in img_url
                            for placeholder_hash in placeholder_hashes
                        )
                        if not is_placeholder:
                            search_image = img_url
                            break

        # Get detailed info
        artist_info = await self.lastfm.get_artist_info(artist_name_clean)

        if not artist_info:
            await self.send_message(
                room, f"❌ Could not fetch details for {artist_name_clean}", client
            )
            return

        # Get genre
        tags = artist_info.get("tags", {})
        if isinstance(tags, dict) and "tag" in tags:
            genre_list = tags["tag"]
            if isinstance(genre_list, list):
                genre = ", ".join([t.get("name", "") for t in genre_list[:3]])
            else:
                genre = genre_list.get("name", "Unknown")
        else:
            genre = "Unknown"

        # Get image - extract the extralarge/largest available
        image = None
        image_list = artist_info.get("image", [])

        # Last.fm's known placeholder image hashes to filter out
        placeholder_hashes = ["2a96cbd8b46e442fc41c2b86b821562f"]

        logger.info(f"Image list for {artist_name_clean}: {image_list}")

        if isinstance(image_list, list) and len(image_list) > 0:
            # Get 'large' size (174s) for more compact display
            for img in image_list:
                if img.get("size") == "large":
                    img_url = img.get("#text", "").strip()
                    if img_url and img_url != "" and "/noimage" not in img_url.lower():
                        is_placeholder = any(
                            placeholder_hash in img_url
                            for placeholder_hash in placeholder_hashes
                        )
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
                logger.info(
                    f"Trying to fetch image from top album for {artist_name_clean}"
                )
                top_albums = await self.lastfm.get_artist_top_albums(
                    artist_name_clean, limit=1
                )
                if top_albums and len(top_albums) > 0:
                    album_image_list = top_albums[0].get("image", [])
                    if isinstance(album_image_list, list):
                        for img in album_image_list:
                            if img.get("size") == "large":
                                img_url = img.get("#text", "").strip()
                                if img_url and "/noimage" not in img_url.lower():
                                    is_placeholder = any(
                                        h in img_url for h in placeholder_hashes
                                    )
                                    if not is_placeholder:
                                        image = img_url
                                        logger.info(
                                            f"Using image from top album: {image}"
                                        )
                                        break

                if not image:
                    logger.info(
                        f"No valid image available for {artist_name_clean} at all"
                    )

        # Get artist listeners and stats
        listeners = artist_info.get("stats", {}).get("listeners", "N/A")
        scrobbles = artist_info.get("stats", {}).get("playcount", "N/A")

        # Format numbers safely
        try:
            listeners_formatted = f"{int(listeners):,}" if listeners != "N/A" else "N/A"
        except (ValueError, TypeError):
            listeners_formatted = "N/A"

        try:
            scrobbles_formatted = f"{int(scrobbles):,}" if scrobbles != "N/A" else "N/A"
        except (ValueError, TypeError):
            scrobbles_formatted = "N/A"

        # Build embed with artist leaderboard
        room_members = list(room.users.keys())
        user_mapping = await self.db.get_all_users_in_room(room.room_id, room_members)
        artist_cache_key = (
            self._normalize_cache_text(artist_name_clean) or artist_name_clean
        )

        # Fetch each user's playcount for this specific artist (with caching)
        room_listeners = []
        for matrix_user, lastfm_user in user_mapping.items():
            try:
                # Check cache first
                cached_playcount = await self.db.get_cached_playcount(
                    lastfm_user, "artist", artist_cache_key, max_age_hours=1
                )

                if cached_playcount is not None:
                    playcount = cached_playcount
                    logger.debug(
                        f"Using cached playcount for {lastfm_user}/{artist_name_clean}: {playcount}"
                    )
                else:
                    # Get artist info with user's playcount from API
                    artist_data = await self.lastfm.get_artist_info(
                        artist_name_clean, username=lastfm_user
                    )
                    if artist_data and "stats" in artist_data:
                        user_playcount = artist_data["stats"].get("userplaycount", "0")
                        playcount = int(user_playcount) if user_playcount else 0
                        # Cache the result
                        await self.db.cache_playcount(
                            lastfm_user, "artist", artist_cache_key, playcount
                        )
                        logger.debug(
                            f"Cached playcount for {lastfm_user}/{artist_name_clean}: {playcount}"
                        )
                    else:
                        playcount = 0

                if playcount > 0:
                    room_listeners.append({"user": lastfm_user, "plays": playcount})
            except Exception as e:
                logger.error(f"Error fetching artist playcount for {lastfm_user}: {e}")
                pass

        room_listeners.sort(key=lambda x: x["plays"], reverse=True)

        # Send image as separate message (downloaded from Last.fm and uploaded to Matrix)
        # Only if we have a valid non-placeholder image
        if (
            image
            and "/noimage" not in image.lower()
            and "2a96cbd8b46e442fc41c2b86b821562f" not in image
        ):
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
            medals = ["👑", "🥈", "🥉"]
            for i, listener in enumerate(room_listeners[:5], 1):  # Show top 5 only
                medal = medals[i - 1] if i <= 3 else f"{i}."
                user_url = f"https://www.last.fm/user/{listener['user']}"
                html_parts.append(
                    f"<br/>{medal} <a href='{user_url}'>{listener['user']}</a> · {listener['plays']:,}"
                )

        html = "\n".join(html_parts)

        # Build plain text version
        artist_url = f"https://www.last.fm/music/{artist_name_clean.replace(' ', '+')}"
        body_lines = [f"{artist_name_clean} - {artist_url}", genre, ""]

        if room_listeners:
            medals_text = ["👑", "🥈", "🥉"]
            for i, listener in enumerate(room_listeners[:5], 1):  # Show top 5 only
                medal = medals_text[i - 1] if i <= 3 else f"{i}."
                user_url = f"https://www.last.fm/user/{listener['user']}"
                body_lines.append(
                    f"{medal} {listener['user']} ({user_url}) · {listener['plays']:,}"
                )
        body = "\n".join(body_lines)

        # Send embed
        await client.room_send(
            room_id=room.room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": body,
                "format": "org.matrix.custom.html",
                "formatted_body": html,
            },
        )

    async def who_knows_track(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
        """Show who in the room listens to this track."""
        if not args:
            now_playing = await self._get_now_playing_context(room, sender, client)
            if not now_playing:
                return
            track_query = f"{now_playing['artist']} {now_playing['track']}".strip()
            if not track_query:
                await self.send_message(
                    room, "❌ No track found for your current listen", client
                )
                return
        else:
            track_query = " ".join(args)

        # Search for the track to get the canonical name
        tracks = await self.lastfm.search_track(track_query, limit=10)
        if not tracks:
            await self.send_message(
                room, f"❌ No tracks found matching '{track_query}'", client
            )
            return

        # Pick closest + most popular result
        top_track = (
            self._select_best_lastfm_result(tracks, track_query, "track") or tracks[0]
        )
        track_name = top_track.get("name", track_query)
        artist_name = self._extract_artist_name(top_track.get("artist", {}))
        track_cache_key = self._normalize_cache_text(track_name) or track_name
        artist_cache_key = self._normalize_cache_text(artist_name) or artist_name

        # Get room members and their Last.fm accounts
        room_members = list(room.users.keys())
        user_mapping = await self.db.get_all_users_in_room(room.room_id, room_members)

        if not user_mapping:
            await self.send_message(
                room,
                "❌ No users in this room have linked their Last.fm accounts",
                client,
            )
            return

        # Fetch each user's playcount for this specific track (with caching)
        room_listeners = []
        for matrix_user, lastfm_user in user_mapping.items():
            try:
                # Check cache first
                cached_playcount = await self.db.get_cached_playcount(
                    lastfm_user,
                    "track",
                    track_cache_key,
                    artist_name=artist_cache_key,
                    max_age_hours=1,
                )

                if cached_playcount is not None:
                    playcount = cached_playcount
                    logger.debug(
                        f"Using cached playcount for {lastfm_user}/{artist_name}/{track_name}: {playcount}"
                    )
                else:
                    # Get track info with user's playcount from API
                    track_data = await self.lastfm.get_track_info(
                        artist_name, track_name, username=lastfm_user
                    )
                    if track_data and "userplaycount" in track_data:
                        playcount = (
                            int(track_data["userplaycount"])
                            if track_data["userplaycount"]
                            else 0
                        )
                        # Cache the result
                        await self.db.cache_playcount(
                            lastfm_user,
                            "track",
                            track_cache_key,
                            playcount,
                            artist_name=artist_cache_key,
                        )
                        logger.debug(
                            f"Cached playcount for {lastfm_user}/{artist_name}/{track_name}: {playcount}"
                        )
                    else:
                        playcount = 0

                if playcount > 0:
                    room_listeners.append(
                        {
                            "user": lastfm_user,
                            "track": track_name,
                            "artist": artist_name,
                            "plays": playcount,
                        }
                    )
            except Exception as e:
                logger.error(f"Error fetching track playcount for {lastfm_user}: {e}")
                pass

        if not room_listeners:
            await self.send_message(
                room,
                f"❌ No one in this room has listened to '{track_name}' by {artist_name}",
                client,
            )
            return

        # Sort by plays
        room_listeners.sort(key=lambda x: x["plays"], reverse=True)

        # Build HTML message
        html_parts = []
        first_listener = room_listeners[0]
        track_url = f"https://www.last.fm/music/{first_listener['artist'].replace(' ', '+')}/_/{first_listener['track'].replace(' ', '+')}"
        html_parts.append(
            f"<b><a href='{track_url}'>{first_listener['track']}</a></b> by {first_listener['artist']}"
        )
        html_parts.append("<br/>")

        medals = ["👑", "🥈", "🥉"]
        for i, listener in enumerate(room_listeners[:5], 1):
            medal = medals[i - 1] if i <= 3 else f"{i}."
            user_url = f"https://www.last.fm/user/{listener['user']}"
            html_parts.append(
                f"<br/>{medal} <a href='{user_url}'>{listener['user']}</a> · {listener['plays']:,}"
            )

        html = "\n".join(html_parts)

        # Build plain text version
        body_lines = [
            f"{first_listener['track']} by {first_listener['artist']} - {track_url}",
            "",
        ]
        for i, listener in enumerate(room_listeners[:5], 1):
            medal = medals[i - 1] if i <= 3 else f"{i}."
            user_url = f"https://www.last.fm/user/{listener['user']}"
            body_lines.append(
                f"{medal} {listener['user']} ({user_url}) · {listener['plays']:,}"
            )
        body = "\n".join(body_lines)

        await client.room_send(
            room_id=room.room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": body,
                "format": "org.matrix.custom.html",
                "formatted_body": html,
            },
        )

    async def who_knows_album(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
        """Show who in the room listens to this album."""
        if not args:
            now_playing = await self._get_now_playing_context(room, sender, client)
            if not now_playing:
                return
            album_name = now_playing["album"]
            artist_name = now_playing["artist"]
            if not album_name:
                await self.send_message(
                    room, "❌ No album found for your current track", client
                )
                return
            if not artist_name:
                await self.send_message(
                    room, "❌ No artist found for your current track", client
                )
                return
        else:
            album_query = " ".join(args)

            # Search for the album to get the canonical name
            albums = await self.lastfm.search_album(album_query, limit=10)
            if not albums:
                await self.send_message(
                    room, f"❌ No albums found matching '{album_query}'", client
                )
                return

            # Pick closest + most popular result
            top_album = (
                self._select_best_lastfm_result(albums, album_query, "album")
                or albums[0]
            )
            album_name = top_album.get("name", album_query)
            artist_name = (
                self._extract_artist_name(top_album.get("artist", {}))
                if "artist" in top_album
                else top_album.get("artist", "Unknown")
            )
        album_cache_key = self._normalize_cache_text(album_name) or album_name
        artist_cache_key = self._normalize_cache_text(artist_name) or artist_name

        # Get room members and their Last.fm accounts
        room_members = list(room.users.keys())
        user_mapping = await self.db.get_all_users_in_room(room.room_id, room_members)

        if not user_mapping:
            await self.send_message(
                room,
                "❌ No users in this room have linked their Last.fm accounts",
                client,
            )
            return

        # Fetch each user's playcount for this specific album (with caching)
        room_listeners = []
        for matrix_user, lastfm_user in user_mapping.items():
            try:
                # Check cache first
                cached_playcount = await self.db.get_cached_playcount(
                    lastfm_user,
                    "album",
                    album_cache_key,
                    artist_name=artist_cache_key,
                    max_age_hours=1,
                )

                if cached_playcount is not None:
                    playcount = cached_playcount
                    logger.debug(
                        f"Using cached playcount for {lastfm_user}/{artist_name}/{album_name}: {playcount}"
                    )
                else:
                    # Get album info with user's playcount from API
                    album_data = await self.lastfm.get_album_info(
                        artist_name, album_name, username=lastfm_user
                    )
                    if album_data and "userplaycount" in album_data:
                        playcount = (
                            int(album_data["userplaycount"])
                            if album_data["userplaycount"]
                            else 0
                        )
                        # Cache the result
                        await self.db.cache_playcount(
                            lastfm_user,
                            "album",
                            album_cache_key,
                            playcount,
                            artist_name=artist_cache_key,
                        )
                        logger.debug(
                            f"Cached playcount for {lastfm_user}/{artist_name}/{album_name}: {playcount}"
                        )
                    else:
                        playcount = 0

                if playcount > 0:
                    room_listeners.append(
                        {
                            "user": lastfm_user,
                            "album": album_name,
                            "artist": artist_name,
                            "plays": playcount,
                        }
                    )
            except Exception as e:
                logger.error(f"Error fetching album playcount for {lastfm_user}: {e}")
                pass

        if not room_listeners:
            await self.send_message(
                room,
                f"❌ No one in this room has listened to '{album_name}' by {artist_name}",
                client,
            )
            return

        # Sort by plays
        room_listeners.sort(key=lambda x: x["plays"], reverse=True)

        # Build HTML message
        html_parts = []
        first_listener = room_listeners[0]
        album_url = f"https://www.last.fm/music/{first_listener['artist'].replace(' ', '+')}/_/{first_listener['album'].replace(' ', '+')}"
        html_parts.append(
            f"<b><a href='{album_url}'>{first_listener['album']}</a></b> by {first_listener['artist']}"
        )
        html_parts.append("<br/>")

        medals = ["👑", "🥈", "🥉"]
        for i, listener in enumerate(room_listeners[:5], 1):
            medal = medals[i - 1] if i <= 3 else f"{i}."
            user_url = f"https://www.last.fm/user/{listener['user']}"
            html_parts.append(
                f"<br/>{medal} <a href='{user_url}'>{listener['user']}</a> · {listener['plays']:,}"
            )

        html = "\n".join(html_parts)

        # Build plain text version
        body_lines = [
            f"{first_listener['album']} by {first_listener['artist']} - {album_url}",
            "",
        ]
        for i, listener in enumerate(room_listeners[:5], 1):
            medal = medals[i - 1] if i <= 3 else f"{i}."
            user_url = f"https://www.last.fm/user/{listener['user']}"
            body_lines.append(
                f"{medal} {listener['user']} ({user_url}) · {listener['plays']:,}"
            )
        body = "\n".join(body_lines)

        await client.room_send(
            room_id=room.room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": body,
                "format": "org.matrix.custom.html",
                "formatted_body": html,
            },
        )

    async def generate_chart(
        self, room: MatrixRoom, sender: str, args: list, client: AsyncClient
    ):
        """Generate a collage chart of top albums."""
        # Parse arguments: size, period, and flags
        size = "3x3"
        period = "7days"
        skip_empty = False
        show_titles = True

        # Filter out flags
        filtered_args = []
        for arg in args:
            if arg.lower() in ["--skipempty", "--skip-empty", "-s"]:
                skip_empty = True
            elif arg.lower() in ["--notitles", "--no-titles", "--notitle", "-n"]:
                show_titles = False
            else:
                filtered_args.append(arg)

        if filtered_args:
            # Check if first arg is a size (NxN format)
            if "x" in filtered_args[0].lower():
                size = filtered_args[0].lower()
                if len(filtered_args) > 1:
                    period = self.normalize_period(filtered_args[1])
            else:
                # First arg is period
                period = self.normalize_period(filtered_args[0])
                if len(filtered_args) > 1 and "x" in filtered_args[1].lower():
                    size = filtered_args[1].lower()

        # Validate size
        try:
            rows, cols = map(int, size.split("x"))
            if rows < 2 or rows > 10 or cols < 2 or cols > 10:
                await self.send_message(
                    room, "❌ Chart size must be between 2x2 and 10x10", client
                )
                return
        except:
            await self.send_message(
                room,
                f"❌ Invalid size format '{size}'. Use format like 3x3, 4x4, 5x5",
                client,
            )
            return

        # Validate period
        if not await self._validate_period(room, period, client):
            return

        # Get target user
        lastfm_user = await self._get_target_user(room, sender, client)
        if not lastfm_user:
            return

        await self.send_message(
            room, f"⏳ Generating {size} chart for {lastfm_user}...", client
        )

        # Fetch top albums
        total_albums = rows * cols
        albums = await self.lastfm.get_top_albums(
            lastfm_user, period, limit=total_albums
        )

        if not albums:
            await self.send_message(
                room, f"❌ No albums found for {lastfm_user} in this period", client
            )
            return

        cyrillic_needed = False
        for album in albums:
            album_name = album.get("name", "") or ""
            artist_name = self._extract_artist_name(album.get("artist", {}))
            if self._contains_cyrillic(album_name) or self._contains_cyrillic(
                artist_name
            ):
                cyrillic_needed = True
                break

        # Download album cover images
        tile_size = 300
        album_tiles = []  # List of (image, album_name, artist_name, has_cover)

        session = await self.lastfm.get_session()
        for album in albums:
            if len(album_tiles) >= total_albums:
                break

            image_url = None
            album_name = album.get("name", "Unknown")
            artist_name = self._extract_artist_name(album.get("artist", {}))

            # Get extralarge image (300x300)
            album_images = album.get("image", [])
            if isinstance(album_images, list):
                for img in album_images:
                    if img.get("size") == "extralarge":
                        url = img.get("#text", "").strip()
                        if url and "/noimage" not in url.lower():
                            image_url = url
                            break

            # Download image
            if image_url:
                try:
                    async with session.get(
                        image_url, timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            image_data = await resp.read()
                            img = Image.open(BytesIO(image_data))
                            img = img.resize(
                                (tile_size, tile_size), Image.Resampling.LANCZOS
                            )
                            album_tiles.append((img, album_name, artist_name, True))
                            continue
                except Exception as e:
                    logger.warning(f"Failed to download album image: {e}")

            # Skip or create placeholder based on flag
            if skip_empty:
                continue
            else:
                placeholder = Image.new(
                    "RGBA", (tile_size, tile_size), color=(0, 0, 0, 0)
                )
                album_tiles.append((placeholder, album_name, artist_name, False))

        # Pad with placeholders if needed (only if not skipping empty)
        if not skip_empty:
            while len(album_tiles) < total_albums:
                placeholder = Image.new(
                    "RGBA", (tile_size, tile_size), color=(0, 0, 0, 0)
                )
                album_tiles.append((placeholder, "", "", False))

        # Adjust grid size if we have fewer items after filtering
        if skip_empty and len(album_tiles) < total_albums:
            actual_count = len(album_tiles)
            # Try to maintain aspect ratio close to original
            cols = min(cols, actual_count)
            rows = (actual_count + cols - 1) // cols

        # Create collage
        collage_width = cols * tile_size
        collage_height = rows * tile_size
        collage = Image.new(
            "RGBA", (collage_width, collage_height), color=(13, 13, 13, 0)
        )

        # Load font for text overlay
        font = self._load_chart_font(14)
        if cyrillic_needed and not self._is_truetype_font(font):
            logger.warning(
                "Cyrillic text detected, but no TrueType font was loaded. Set CHART_FONT_PATH to a Cyrillic-capable font."
            )

        # Paste album covers with text overlay
        for idx, (img, album_name, artist_name, has_cover) in enumerate(album_tiles):
            if idx >= rows * cols:
                break

            row = idx // cols
            col = idx % cols
            x = col * tile_size
            y = row * tile_size

            # Paste the album cover
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            collage.paste(img, (x, y), img)

            # Add text overlay with artist + album (if enabled)
            if show_titles and (album_name or artist_name):
                self._draw_chart_text(
                    collage, x, y, tile_size, artist_name, album_name, font
                )

        # Get period name for message
        period_name = self._get_period_name(period)

        # Save to BytesIO
        image_buffer = BytesIO()
        collage.save(image_buffer, format="PNG")
        image_buffer.seek(0)

        # Upload to Matrix
        try:
            upload_response, _ = await client.upload(
                image_buffer,
                content_type="image/png",
                filename=f"{lastfm_user}_{size}_{period}_chart.png",
                filesize=len(image_buffer.getvalue()),
            )

            if isinstance(upload_response, UploadResponse):
                await client.room_send(
                    room_id=room.room_id,
                    message_type="m.room.message",
                    content={
                        "msgtype": "m.image",
                        "body": f"{size} {period_name} chart for {lastfm_user}",
                        "url": upload_response.content_uri,
                        "info": {
                            "mimetype": "image/png",
                            "size": len(image_buffer.getvalue()),
                            "w": collage_width,
                            "h": collage_height,
                        },
                    },
                )
                logger.info(f"Chart sent successfully for {lastfm_user}")
            else:
                await self.send_message(
                    room, f"❌ Failed to upload chart image", client
                )
                logger.error(f"Upload failed: {upload_response}")
        except Exception as e:
            await self.send_message(
                room, f"❌ Error generating chart: {str(e)}", client
            )
            logger.error(f"Chart generation error: {e}", exc_info=True)

    async def send_message(self, room: MatrixRoom, message: str, client: AsyncClient):
        """Send a message to the room."""
        response = await client.room_send(
            room_id=room.room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": message,
                "format": "org.matrix.custom.html",
                "formatted_body": self._markdown_to_html(message),
            },
        )
        return response.event_id if hasattr(response, "event_id") else None

    async def send_paginated_message(
        self,
        room: MatrixRoom,
        message: str,
        client: AsyncClient,
        user_id: str,
        current_page: int,
        total_pages: int,
        callback: Callable,
    ) -> Optional[str]:
        """Send a message with pagination support."""
        event_id = await self.send_message(room, message, client)
        logger.debug(f"Sent message with event_id: {event_id}")

        if event_id and total_pages > 1:
            logger.debug(
                f"Registering pagination for event {event_id}: page {current_page}/{total_pages}, user {user_id}"
            )
            # Register pagination
            self.pagination.register(
                event_id, room.room_id, user_id, current_page, total_pages, callback
            )
            logger.debug(
                f"Pagination registered. Active paginations: {list(self.pagination.paginations.keys())}"
            )

            # Add initial reaction arrows so users know they can click
            import asyncio

            await asyncio.sleep(0.1)  # Small delay to ensure message is processed

            logger.debug(f"Adding ⬅️ reaction to {event_id}")
            await client.room_send(
                room_id=room.room_id,
                message_type="m.reaction",
                content={
                    "m.relates_to": {
                        "rel_type": "m.annotation",
                        "event_id": event_id,
                        "key": "⬅️",
                    }
                },
            )

            logger.debug(f"Adding ➡️ reaction to {event_id}")
            await client.room_send(
                room_id=room.room_id,
                message_type="m.reaction",
                content={
                    "m.relates_to": {
                        "rel_type": "m.annotation",
                        "event_id": event_id,
                        "key": "➡️",
                    }
                },
            )
            logger.debug(f"Initial reactions added to {event_id}")
        else:
            logger.debug(
                f"Not adding pagination - event_id: {event_id}, total_pages: {total_pages}"
            )

        return event_id

    async def edit_message(
        self, room: MatrixRoom, event_id: str, new_message: str, client: AsyncClient
    ):
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
                    "formatted_body": self._markdown_to_html(new_message),
                },
                "m.relates_to": {"rel_type": "m.replace", "event_id": event_id},
            },
        )
        logger.info(f"Message edited successfully")

    async def send_image_message(
        self, room: MatrixRoom, image_url: str, artist_name: str, client: AsyncClient
    ):
        """Download image from Last.fm and upload to Matrix, then send."""
        try:
            # Download the image
            session = await self.lastfm.get_session()
            async with session.get(
                image_url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to download image: HTTP {resp.status}")
                    return

                image_data = await resp.read()
                content_type = resp.headers.get("Content-Type", "image/png")

                logger.info(
                    f"Downloaded image: {len(image_data)} bytes, Content-Type: {content_type}"
                )
                logger.info(f"First 50 bytes: {image_data[:50]}")

                # Verify we got actual image data
                if len(image_data) < 100:
                    logger.error(
                        f"Image data too small ({len(image_data)} bytes), likely not a valid image"
                    )
                    return

                # Check for PNG or JPEG magic bytes
                if not (
                    image_data[:8] == b"\x89PNG\r\n\x1a\n"
                    or image_data[:2] == b"\xff\xd8"
                ):
                    logger.error(
                        f"Invalid image format. Magic bytes: {image_data[:10]}"
                    )
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
                filesize=file_size,
            )

            logger.info(
                f"Upload response type: {type(upload_response)}, response: {upload_response}"
            )

            if isinstance(upload_response, UploadError):
                logger.error(f"Failed to upload image: {upload_response.message}")
                return

            if (
                not isinstance(upload_response, UploadResponse)
                or not upload_response.content_uri
            ):
                logger.error(
                    f"Failed to upload image to Matrix. Response: {upload_response}"
                )
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
                    },
                },
            )
            logger.info(
                f"Successfully uploaded and sent image: {upload_response.content_uri}"
            )

        except Exception as e:
            logger.error(f"Error sending image: {e}", exc_info=True)

    @staticmethod
    def _markdown_to_html(text: str) -> str:
        """Convert basic markdown to HTML."""
        # Links - must be before bold/italic to avoid conflicts
        text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
        # Bold
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        # Italic
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
        # Code - escape HTML entities inside code blocks so <username> etc. render
        def _code_replace(m):
            inner = m.group(1).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            return f"<code>{inner}</code>"
        text = re.sub(r"`(.+?)`", _code_replace, text)
        # Newlines
        text = text.replace("\n", "<br/>")
        return text
