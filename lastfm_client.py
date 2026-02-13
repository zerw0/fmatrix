"""
Last.fm API client
"""

import logging
import aiohttp
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://ws.audioscrobbler.com/2.0"


class LastfmClient:
    """Client for interacting with Last.fm API."""

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        """Close the session."""
        if self.session:
            await self.session.close()

    async def _request(self, params: Dict) -> Optional[Dict]:
        """Make a request to Last.fm API."""
        params['api_key'] = self.api_key
        params['format'] = 'json'

        session = await self.get_session()

        try:
            async with session.get(BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.error(f"Last.fm API error: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"Error calling Last.fm API: {e}")
            return None

    async def get_user_info(self, username: str) -> Optional[Dict]:
        """Get user info from Last.fm."""
        data = await self._request({
            'method': 'user.getinfo',
            'user': username
        })

        if data and 'user' in data:
            return data['user']
        return None

    async def get_top_artists(self, username: str, period: str = 'overall', limit: int = 10) -> List[Dict]:
        """Get user's top artists."""
        # Normalize period: 7days -> 7day (Last.fm API compatibility)
        period = period.replace('7days', '7day')
        data = await self._request({
            'method': 'user.gettopartists',
            'user': username,
            'period': period,
            'limit': limit
        })

        if data and 'topartists' in data:
            artists = data['topartists'].get('artist', [])
            if isinstance(artists, dict):
                return [artists]
            return artists
        return []

    async def get_top_tracks(self, username: str, period: str = 'overall', limit: int = 10) -> List[Dict]:
        """Get user's top tracks."""
        # Normalize period: 7days -> 7day (Last.fm API compatibility)
        period = period.replace('7days', '7day')
        data = await self._request({
            'method': 'user.gettoptracks',
            'user': username,
            'period': period,
            'limit': limit
        })

        if data and 'toptracks' in data:
            tracks = data['toptracks'].get('track', [])
            if isinstance(tracks, dict):
                return [tracks]
            return tracks
        return []

    async def get_top_albums(self, username: str, period: str = 'overall', limit: int = 10) -> List[Dict]:
        """Get user's top albums."""
        # Normalize period: 7days -> 7day (Last.fm API compatibility)
        period = period.replace('7days', '7day')
        data = await self._request({
            'method': 'user.gettopalbums',
            'user': username,
            'period': period,
            'limit': limit
        })

        if data and 'topalbums' in data:
            albums = data['topalbums'].get('album', [])
            if isinstance(albums, dict):
                return [albums]
            return albums
        return []

    async def get_recent_tracks(self, username: str, limit: int = 10) -> List[Dict]:
        """Get user's recent tracks."""
        data = await self._request({
            'method': 'user.getrecenttracks',
            'user': username,
            'limit': limit
        })

        if data and 'recenttracks' in data:
            tracks = data['recenttracks'].get('track', [])
            if isinstance(tracks, dict):
                return [tracks]
            return tracks
        return []

    async def get_user_stats(self, username: str) -> Optional[Dict]:
        """Get user's overall stats."""
        user_info = await self.get_user_info(username)
        if not user_info:
            return None

        stats = {
            'username': user_info.get('name'),
            'real_name': user_info.get('realname', 'N/A'),
            'play_count': int(user_info.get('playcount', 0)),
            'artist_count': int(user_info.get('artist_count', 0)),
            'track_count': int(user_info.get('track_count', 0)),
            'album_count': int(user_info.get('album_count', 0)),
        }

        return stats

    async def search_artist(self, artist_name: str, limit: int = 10) -> List[Dict]:
        """Search for an artist."""
        data = await self._request({
            'method': 'artist.search',
            'artist': artist_name,
            'limit': limit
        })

        if data and 'results' in data and 'artistmatches' in data['results']:
            artists = data['results']['artistmatches'].get('artist', [])
            if isinstance(artists, dict):
                return [artists]
            return artists
        return []

    async def search_track(self, track_name: str, limit: int = 10) -> List[Dict]:
        """Search for a track."""
        data = await self._request({
            'method': 'track.search',
            'track': track_name,
            'limit': limit
        })

        if data and 'results' in data and 'trackmatches' in data['results']:
            tracks = data['results']['trackmatches'].get('track', [])
            if isinstance(tracks, dict):
                return [tracks]
            return tracks
        return []

    async def search_album(self, album_name: str, limit: int = 10) -> List[Dict]:
        """Search for an album."""
        data = await self._request({
            'method': 'album.search',
            'album': album_name,
            'limit': limit
        })

        if data and 'results' in data and 'albummatches' in data['results']:
            albums = data['results']['albummatches'].get('album', [])
            if isinstance(albums, dict):
                return [albums]
            return albums
        return []

    async def get_artist_info(self, artist_name: str) -> Optional[Dict]:
        """Get detailed info about an artist including image and genre."""
        data = await self._request({
            'method': 'artist.getinfo',
            'artist': artist_name
        })

        if data and 'artist' in data:
            return data['artist']
        return None

    async def get_artist_top_albums(self, artist_name: str, limit: int = 10) -> List[Dict]:
        """Get an artist's top albums."""
        data = await self._request({
            'method': 'artist.gettopalbums',
            'artist': artist_name,
            'limit': limit
        })

        if data and 'topalbums' in data and 'album' in data['topalbums']:
            albums = data['topalbums']['album']
            if isinstance(albums, dict):
                return [albums]
            return albums
        return []
