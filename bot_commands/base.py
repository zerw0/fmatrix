from __future__ import annotations

"""
Command handler for bot commands
"""

import logging
import os
import re
import time
import aiohttp
from difflib import SequenceMatcher
from io import BytesIO
from typing import Optional, Dict, Callable, Any
from nio import AsyncClient, MatrixRoom
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


class CommandHandlerBase:
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
        'spotify': 'spotify',
        'sp': 'spotify',
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

    def __init__(self, db: Database, lastfm: LastfmClient, discogs, spotify, config: Config):
        self.db = db
        self.lastfm = lastfm
        self.discogs = discogs
        self.spotify = spotify
        self.config = config
        self.pagination = PaginationManager()
        self._now_playing_cache: Dict[str, Dict[str, Any]] = {}
        self._now_playing_ttl_seconds = 10

    @staticmethod
    def normalize_command(cmd: str) -> str:
        """Convert command abbreviations to full names."""
        return CommandHandlerBase.COMMAND_ALIASES.get(cmd.lower(), cmd.lower())

    @staticmethod
    def normalize_period(period: str) -> str:
        """Convert period abbreviations to full names."""
        return CommandHandlerBase.PERIOD_ALIASES.get(period.lower(), period.lower())

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

    @staticmethod
    def _normalize_fuzzy_text(text: str) -> str:
        if not text:
            return ''
        return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

    @staticmethod
    def _normalize_cache_text(text: str) -> str:
        if not text:
            return ''
        return re.sub(r"\s+", " ", text.strip().lower())

    @classmethod
    def _fuzzy_ratio(cls, left: str, right: str) -> float:
        left_norm = cls._normalize_fuzzy_text(left)
        right_norm = cls._normalize_fuzzy_text(right)
        if not left_norm or not right_norm:
            return 0.0
        return SequenceMatcher(None, left_norm, right_norm).ratio()

    @staticmethod
    def _safe_int(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _contains_cyrillic(text: str) -> bool:
        if not text:
            return False
        return any(0x0400 <= ord(ch) <= 0x04FF for ch in text)

    @staticmethod
    def _is_truetype_font(font: ImageFont.ImageFont) -> bool:
        return isinstance(font, ImageFont.FreeTypeFont)

    def _load_chart_font(self, font_size: int) -> ImageFont.ImageFont:
        font_candidates = []
        if self.config.chart_font_path:
            font_candidates.append(os.path.expanduser(self.config.chart_font_path))

        font_candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
            "/Library/Fonts/Arial.ttf",
        ])

        for font_path in font_candidates:
            if not font_path or not os.path.exists(font_path):
                continue
            try:
                return ImageFont.truetype(font_path, font_size)
            except Exception:
                continue

        return ImageFont.load_default()

    def _draw_chart_text(
        self,
        collage: Image.Image,
        x: int,
        y: int,
        tile_size: int,
        artist_name: str,
        album_name: str,
        font: ImageFont.ImageFont
    ) -> None:
        if not (artist_name or album_name):
            return

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

        lines = [line for line in lines if line]
        if not lines:
            return

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

        block_x = x + (tile_size - max_line_width) // 2
        block_y = y + tile_size - total_height - 10

        line_y = block_y
        for line, line_width, line_height in line_metrics:
            line_x = block_x + (max_line_width - line_width) // 2
            shadow_offset = 1
            draw.text(
                (line_x + shadow_offset, line_y + shadow_offset),
                line,
                fill=(0, 0, 0, 160),
                font=font
            )
            draw.text((line_x, line_y), line, fill='#FFFFFF', font=font)
            line_y += line_height + line_spacing

    def _lastfm_candidate_text(self, result: Dict[str, Any], kind: str) -> str:
        name = result.get('name', '')
        if kind == 'track':
            artist_name = self._extract_artist_name(result.get('artist', {}))
            if artist_name:
                return f"{artist_name} - {name}".strip()
        if kind == 'album':
            artist_name = self._extract_artist_name(result.get('artist', {}))
            if artist_name:
                return f"{artist_name} - {name}".strip()
        return name

    def _get_lastfm_popularity(self, result: Dict[str, Any]) -> int:
        return max(
            self._safe_int(result.get('listeners')),
            self._safe_int(result.get('playcount'))
        )

    def _select_best_lastfm_result(self, results: list, query: str, kind: str) -> Optional[Dict[str, Any]]:
        if not results:
            return None

        max_popularity = max((self._get_lastfm_popularity(r) for r in results), default=0)
        best_result = None
        best_score = -1.0
        best_similarity = -1.0

        for result in results:
            candidate = self._lastfm_candidate_text(result, kind)
            similarity = self._fuzzy_ratio(query, candidate)
            popularity = self._get_lastfm_popularity(result)
            popularity_score = (popularity / max_popularity) if max_popularity else 0.0
            score = (similarity * 0.75) + (popularity_score * 0.25)

            if score > best_score or (abs(score - best_score) < 1e-6 and similarity > best_similarity):
                best_result = result
                best_score = score
                best_similarity = similarity

        return best_result

    def _get_discogs_popularity(self, result: Dict[str, Any]) -> int:
        community = result.get('community')
        if isinstance(community, dict):
            return self._safe_int(community.get('have')) + self._safe_int(community.get('want'))
        return 0

    def _select_best_discogs_result(self, results: list, query: str) -> Optional[Dict[str, Any]]:
        if not results:
            return None

        max_popularity = max((self._get_discogs_popularity(r) for r in results), default=0)
        best_result = None
        best_score = -1.0
        best_similarity = -1.0

        for result in results:
            title = result.get('title', '')
            similarity = self._fuzzy_ratio(query, title)
            popularity = self._get_discogs_popularity(result)
            popularity_score = (popularity / max_popularity) if max_popularity else 0.0
            score = (similarity * 0.8) + (popularity_score * 0.2)

            if score > best_score or (abs(score - best_score) < 1e-6 and similarity > best_similarity):
                best_result = result
                best_score = score
                best_similarity = similarity

        return best_result

    def _select_best_spotify_result(self, results: list, query: str) -> Optional[Dict[str, Any]]:
        """Select the best Spotify track result using fuzzy matching."""
        if not results:
            return None

        best_result = None
        best_score = -1.0
        best_similarity = -1.0

        for result in results:
            # Build candidate text from track name and artist names
            track_name = result.get('name', '')
            artists = [artist.get('name', '') for artist in result.get('artists', [])]
            candidate = f"{track_name} {' '.join(artists)}"
            similarity = self._fuzzy_ratio(query, candidate)

            # Spotify doesn't have popularity in the same way, so just use similarity
            if similarity > best_score or (abs(similarity - best_score) < 1e-6 and similarity > best_similarity):
                best_result = result
                best_score = similarity
                best_similarity = similarity

        return best_result

    def _format_spotify_track(self, track: Dict) -> Dict[str, str]:
        """Format a Spotify track result for display."""
        return {
            'name': track.get('name'),
            'artist': ', '.join([artist.get('name', '') for artist in track.get('artists', [])]),
            'album': track.get('album', {}).get('name'),
            'url': track.get('external_urls', {}).get('spotify'),
            'uri': track.get('uri')
        }

    def _get_period_name(self, period: str) -> str:
        """Get display name for a period."""
        return self.PERIOD_NAMES.get(period, period)


CommandHandler = CommandHandlerBase
