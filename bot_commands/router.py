from __future__ import annotations

import logging
from nio import AsyncClient, MatrixRoom

logger = logging.getLogger(__name__)


class CommandRouterMixin:
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
                    await self.who_knows(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'whoknowstrack':
                    await self.who_knows_track(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'whoknowsalbum':
                    await self.who_knows_album(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'chart':
                    await self.generate_chart(room, sender, args[1:], client)
                elif self.normalize_command(args[0]) == 'leaderboard':
                    await self.show_leaderboard(room, args[1:], client)
                elif self.normalize_command(args[0]) == 'spotify':
                    if not self.spotify:
                        await self.send_message(room, "❌ Spotify integration is not configured.", client)
                        return
                    await self.show_spotify_link(room, sender, args[1:], client)
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
            elif command == 'spotify':
                if not self.spotify:
                    await self.send_message(room, "❌ Spotify integration is not configured.", client)
                    return

                if not args:
                    # No args - show now playing from Last.fm and search on Spotify
                    await self.show_spotify_link(room, sender, [], client)
                else:
                    # Search for specific track
                    await self.show_spotify_link(room, sender, args, client)
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
