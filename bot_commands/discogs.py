from __future__ import annotations

from nio import AsyncClient, MatrixRoom


class DiscogsCommandsMixin:
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
                def slugify(text):
                    import re
                    slug = re.sub(r'[^\w\-]+', '-', text)
                    slug = re.sub(r'-+', '-', slug)
                    return slug.strip('-')

                slug = slugify(f"{artist_name} {title}")
                discogs_url = f"https://www.discogs.com/release/{release_id}-{slug}" if release_id else None

                # Format the line
                if discogs_url:
                    items_text.append(f"[{format_display} {artist_name} - {title}]({discogs_url}) ({year})")
                else:
                    items_text.append(f"{format_display} {artist_name} - {title} ({year})")

            collection_link = f"https://www.discogs.com/user/{discogs_username}/collection"
            collection_text = f"""
📀 **[{discogs_username}'s Collection]({collection_link})** (Page {page_num}/{total_pages})
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
                def slugify(text):
                    import re
                    slug = re.sub(r'[^\w\-]+', '-', text)
                    slug = re.sub(r'-+', '-', slug)
                    return slug.strip('-')

                slug = slugify(f"{artist_name} {title}")
                discogs_url = f"https://www.discogs.com/release/{release_id}-{slug}" if release_id else None

                # Format the line
                if discogs_url:
                    items_text.append(f"[{format_display} {artist_name} - {title}]({discogs_url}) ({year})")
                else:
                    items_text.append(f"{format_display} {artist_name} - {title} ({year})")

            wantlist_link = f"https://www.discogs.com/user/{discogs_username}/wantlist"
            wantlist_text = f"""
🎯 **[{discogs_username}'s Wantlist]({wantlist_link})** (Page {page_num}/{total_pages})
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
            # Check for Discogs outage or site error
            outage_message = "Discogs may be temporarily unavailable. Please check https://status.discogs.com for site status."
            await self.send_message(
                room,
                f"❌ No results found for '{query}'. {outage_message}",
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

            if resource_url:
                discogs_id = resource_url.rstrip('/').split('/')[-1]
                item_type_lower = item_type.lower()
                if item_type_lower == 'release':
                    release_info = await self.discogs.get_release(discogs_id)
                    if release_info:
                        discogs_link = f"https://www.discogs.com/release/{discogs_id}"
                        country = release_info.get('country', 'Unknown')
                        label_list = release_info.get('labels', [])
                        label = label_list[0].get('name', 'Unknown') if label_list else 'Unknown'
                        format_list = release_info.get('formats', [])
                        format_str = ', '.join([fmt.get('name', '') for fmt in format_list]) if format_list else 'Unknown'
                        edition = release_info.get('title', '')
                        catno = label_list[0].get('catno', '') if label_list else ''
                        extra = f"Country: {country} | Label: {label} | Format: {format_str}"
                        if catno:
                            extra += f" | Cat#: {catno}"
                        items_text.append(f"• Release: [{title}]({discogs_link}){year_str}\n    {extra}")
                    else:
                        items_text.append(f"• [Release] {title}{year_str} (unavailable)")
                elif item_type_lower == 'master':
                    discogs_link = f"https://www.discogs.com/master/{discogs_id}"
                    items_text.append(f"• Master: [{title}]({discogs_link}){year_str}")
                elif item_type_lower == 'artist':
                    discogs_link = f"https://www.discogs.com/artist/{discogs_id}"
                    items_text.append(f"• Artist: [{title}]({discogs_link}){year_str}")
                else:
                    items_text.append(f"• {item_type}: {title}{year_str}")
            else:
                items_text.append(f"• {item_type}: {title}{year_str}")

        search_text = f"🔍 **Discogs Search Results** for '{query}':\n\n" + '\n'.join(items_text)
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
        artists = await self.discogs.search_artist(artist_name, limit=10)

        if not artists:
            await self.send_message(
                room,
                f"❌ No artist found for '{artist_name}'.",
                client
            )
            return

        best_artist = self._select_best_discogs_result(artists, artist_name) or artists[0]
        artist_id = best_artist.get('id')
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
        releases = await self.discogs.search_release(release_name, limit=10)

        if not releases:
            await self.send_message(
                room,
                f"❌ No release found for '{release_name}'.",
                client
            )
            return

        best_release = self._select_best_discogs_result(releases, release_name) or releases[0]
        release_id = best_release.get('id')
        release_info = await self.discogs.get_release(release_id)

        if not release_info:
            await self.send_message(
                room,
                f"❌ Release info unavailable. The Discogs page may be removed or restricted. Try searching for another release or check Discogs directly.",
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
