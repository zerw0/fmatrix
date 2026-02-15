"""
Last.fm API client
"""

import json
import logging
import ssl
import time
import aiohttp
import certifi
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://ws.audioscrobbler.com/2.0"


class LastfmClient:
    """Client for interacting with Last.fm API."""

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, tuple[float, Dict]] = {}
        self._cache_ttls = {
            'user.gettopartists': 600,
            'user.gettoptracks': 600,
            'user.gettopalbums': 600,
            'user.getinfo': 300,
            'user.getrecenttracks': 30,
            'artist.search': 300,
            'track.search': 300,
            'album.search': 300,
            'artist.getinfo': 3600,
            'artist.gettopalbums': 3600,
            'track.getinfo': 3600,
            'user.getlovedtracks': 300,
        }
        self._default_cache_ttl = 120
        self.cache_db = None

    def set_cache_db(self, cache_db):
        """Set database for persistent caching."""
        self.cache_db = cache_db

    def _cache_key(self, params: Dict) -> str:
        parts = []
        for key, value in sorted(params.items()):
            if key in {'api_key', 'format'}:
                continue
            parts.append(f"{key}={value}")
        return "|".join(parts)

    def _cache_ttl(self, params: Dict) -> int:
        method = params.get('method')
        if method in self._cache_ttls:
            return self._cache_ttls[method]
        return self._default_cache_ttl

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

        ttl = self._cache_ttl(params)
        cache_key = self._cache_key(params)
        if ttl > 0:
            cached = self._cache.get(cache_key)
            if cached:
                cached_at, cached_data = cached
                if time.monotonic() - cached_at < ttl:
                    return cached_data
                self._cache.pop(cache_key, None)

            if self.cache_db:
                try:
                    cached_json = await self.cache_db.get_lastfm_cache(cache_key)
                    if cached_json:
                        cached_data = json.loads(cached_json)
                        self._cache[cache_key] = (time.monotonic(), cached_data)
                        return cached_data
                except Exception as e:
                    logger.warning(f"Last.fm cache read failed: {e}")

        session = await self.get_session()

        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            async with session.get(
                BASE_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
                ssl=ssl_context,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if ttl > 0:
                        self._cache[cache_key] = (time.monotonic(), data)
                        if self.cache_db:
                            try:
                                await self.cache_db.set_lastfm_cache(
                                    cache_key,
                                    json.dumps(data),
                                    ttl,
                                )
                            except Exception as e:
                                logger.warning(f"Last.fm cache write failed: {e}")
                    return data
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

    async def get_all_top_artists(
        self,
        username: str,
        period: str = 'overall',
        page_size: int = 200,
        max_pages: int | None = None,
    ) -> List[Dict]:
        """Get all of a user's top artists by paging the Last.fm API."""
        period = period.replace('7days', '7day')
        all_artists: List[Dict] = []
        page = 1
        total_pages = None

        while True:
            if max_pages is not None and page > max_pages:
                break

            data = await self._request({
                'method': 'user.gettopartists',
                'user': username,
                'period': period,
                'limit': page_size,
                'page': page,
            })

            if not data or 'topartists' not in data:
                break

            topartists = data['topartists']
            artists = topartists.get('artist', [])
            if isinstance(artists, dict):
                artists = [artists]

            all_artists.extend(artists)

            if total_pages is None:
                attr = topartists.get('@attr', {})
                total_pages_raw = attr.get('totalPages') or attr.get('totalpages')
                try:
                    total_pages = int(total_pages_raw)
                except (TypeError, ValueError):
                    total_pages = None

            if total_pages is not None and page >= total_pages:
                break

            if not artists or len(artists) < page_size:
                break

            page += 1

        return all_artists

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

    async def get_all_top_tracks(
        self,
        username: str,
        period: str = 'overall',
        page_size: int = 200,
        max_pages: int | None = None,
    ) -> List[Dict]:
        """Get all of a user's top tracks by paging the Last.fm API."""
        period = period.replace('7days', '7day')
        all_tracks: List[Dict] = []
        page = 1
        total_pages = None

        while True:
            if max_pages is not None and page > max_pages:
                break

            data = await self._request({
                'method': 'user.gettoptracks',
                'user': username,
                'period': period,
                'limit': page_size,
                'page': page,
            })

            if not data or 'toptracks' not in data:
                break

            toptracks = data['toptracks']
            tracks = toptracks.get('track', [])
            if isinstance(tracks, dict):
                tracks = [tracks]

            all_tracks.extend(tracks)

            if total_pages is None:
                attr = toptracks.get('@attr', {})
                total_pages_raw = attr.get('totalPages') or attr.get('totalpages')
                try:
                    total_pages = int(total_pages_raw)
                except (TypeError, ValueError):
                    total_pages = None

            if total_pages is not None and page >= total_pages:
                break

            if not tracks or len(tracks) < page_size:
                break

            page += 1

        return all_tracks

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

    async def get_all_top_albums(
        self,
        username: str,
        period: str = 'overall',
        page_size: int = 200,
        max_pages: int | None = None,
    ) -> List[Dict]:
        """Get all of a user's top albums by paging the Last.fm API."""
        period = period.replace('7days', '7day')
        all_albums: List[Dict] = []
        page = 1
        total_pages = None

        while True:
            if max_pages is not None and page > max_pages:
                break

            data = await self._request({
                'method': 'user.gettopalbums',
                'user': username,
                'period': period,
                'limit': page_size,
                'page': page,
            })

            if not data or 'topalbums' not in data:
                break

            topalbums = data['topalbums']
            albums = topalbums.get('album', [])
            if isinstance(albums, dict):
                albums = [albums]

            all_albums.extend(albums)

            if total_pages is None:
                attr = topalbums.get('@attr', {})
                total_pages_raw = attr.get('totalPages') or attr.get('totalpages')
                try:
                    total_pages = int(total_pages_raw)
                except (TypeError, ValueError):
                    total_pages = None

            if total_pages is not None and page >= total_pages:
                break

            if not albums or len(albums) < page_size:
                break

            page += 1

        return all_albums

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

    async def get_artist_info(self, artist_name: str, username: str = None) -> Optional[Dict]:
        """Get detailed info about an artist including image and genre.

        If username is provided, includes that user's playcount for the artist.
        """
        params = {
            'method': 'artist.getinfo',
            'artist': artist_name
        }
        if username:
            params['username'] = username

        data = await self._request(params)

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

    async def get_album_info(self, artist_name: str, album_name: str, username: str = None) -> Optional[Dict]:
        """Get detailed info about an album.

        If username is provided, includes that user's playcount for the album.
        """
        params = {
            'method': 'album.getinfo',
            'artist': artist_name,
            'album': album_name
        }
        if username:
            params['username'] = username

        data = await self._request(params)

        if data and 'album' in data:
            return data['album']
        return None

    async def get_now_playing(self, username: str) -> Optional[Dict]:
        """Get user's currently playing track."""
        tracks = await self.get_recent_tracks(username, limit=1)
        if tracks and len(tracks) > 0:
            track = tracks[0]
            # Check if track is currently playing (has @attr with nowplaying key)
            if isinstance(track.get('artist'), dict):
                return track
            elif isinstance(track.get('artist'), str):
                # Single track result
                return track
        return None

    async def get_track_info(self, artist_name: str, track_name: str, username: str = None) -> Optional[Dict]:
        """Get detailed info about a track."""
        params = {
            'method': 'track.getinfo',
            'artist': artist_name,
            'track': track_name
        }
        if username:
            params['username'] = username

        data = await self._request(params)

        if data and 'track' in data:
            return data['track']
        return None

    async def get_user_loved_tracks(self, username: str, limit: int = 10) -> List[Dict]:
        """Get user's loved tracks."""
        data = await self._request({
            'method': 'user.getlovedtracks',
            'user': username,
            'limit': limit
        })

        if data and 'lovedtracks' in data:
            tracks = data['lovedtracks'].get('track', [])
            if isinstance(tracks, dict):
                return [tracks]
            return tracks
        return []

    async def love_track(self, artist_name: str, track_name: str, session_key: str) -> bool:
        """Love a track (requires authenticated session)."""
        logger.info(f"Loving track: {track_name} by {artist_name}")

        sig_params = {
            'method': 'track.love',
            'artist': artist_name,
            'track': track_name,
            'sk': session_key,
            'api_key': self.api_key
        }

        params = {
            'method': 'track.love',
            'artist': artist_name,
            'track': track_name,
            'sk': session_key,
            'api_key': self.api_key,
            'api_sig': self._get_api_signature(sig_params),
            'format': 'json'
        }

        logger.info(f"Params being sent (minus api_sig): method={params['method']}, artist={params['artist']}, track={params['track']}, sk={params['sk'][:10]}..., api_key={params['api_key'][:5]}..., format={params['format']}")

        session = await self.get_session()
        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            async with session.post(
                BASE_URL,
                data=params,
                timeout=aiohttp.ClientTimeout(total=10),
                ssl=ssl_context,
            ) as resp:
                logger.info(f"Last.fm love track response status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(f"Last.fm raw response: {data}")

                    # Check if response is wrapped in 'lfm' key
                    if isinstance(data, dict):
                        # Try to extract from lfm wrapper
                        if 'lfm' in data:
                            lfm_data = data['lfm']
                        else:
                            lfm_data = data

                        # Check for status
                        if lfm_data.get('status') == 'ok':
                            logger.info(f"Successfully loved {track_name} by {artist_name}")
                            return True
                        # Empty response {} often means success with no data
                        elif not data and resp.status == 200:
                            logger.info(f"Empty response from love_track - treating as success")
                            return True
                        else:
                            logger.warning(f"Unexpected response from love_track: {data}")
                            # Check if error key exists
                            if 'error' in data or 'error' in lfm_data:
                                logger.error(f"Last.fm error: {data.get('error') or lfm_data.get('error')}")
                            return False
                else:
                    logger.error(f"Last.fm love track error: {resp.status}")
                    error_text = await resp.text()
                    logger.error(f"Error response: {error_text}")
        except Exception as e:
            logger.error(f"Error loving track: {e}", exc_info=True)

        return False

    async def unlove_track(self, artist_name: str, track_name: str, session_key: str) -> bool:
        """Unlove a track (requires authenticated session)."""
        logger.info(f"Unloving track: {track_name} by {artist_name}")

        sig_params = {
            'method': 'track.unlove',
            'artist': artist_name,
            'track': track_name,
            'sk': session_key,
            'api_key': self.api_key
        }

        params = {
            'method': 'track.unlove',
            'artist': artist_name,
            'track': track_name,
            'sk': session_key,
            'api_key': self.api_key,
            'api_sig': self._get_api_signature(sig_params),
            'format': 'json'
        }

        session = await self.get_session()
        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            async with session.post(
                BASE_URL,
                data=params,
                timeout=aiohttp.ClientTimeout(total=10),
                ssl=ssl_context,
            ) as resp:
                logger.info(f"Last.fm unlove track response status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(f"Last.fm unlove track response: {data}")
                    # Check if response contains status: ok
                    if isinstance(data, dict) and data.get('status') == 'ok':
                        logger.info(f"Successfully unloved {track_name} by {artist_name}")
                        return True
                    else:
                        logger.warning(f"Unexpected response from unlove_track: {data}")
                        return False
                else:
                    logger.error(f"Last.fm unlove track error: {resp.status}")
                    error_text = await resp.text()
                    logger.error(f"Error response: {error_text}")
        except Exception as e:
            logger.error(f"Error unloving track: {e}", exc_info=True)

        return False

    def _get_api_signature(self, params: Dict) -> str:
        """Generate API signature for authenticated requests."""
        import hashlib
        # Sort params and create signature string
        items = sorted(params.items())
        sig_string = ''.join([f"{k}{v}" for k, v in items]) + self.api_secret
        return hashlib.md5(sig_string.encode()).hexdigest()

    async def get_auth_token(self) -> Optional[str]:
        """Get an authorization token for the auth flow."""
        sig_params = {
            'method': 'auth.gettoken',
            'api_key': self.api_key
        }

        params = {
            'method': 'auth.gettoken',
            'api_key': self.api_key,
            'api_sig': self._get_api_signature(sig_params),
            'format': 'json'
        }

        data = await self._request(params)

        if data and 'token' in data:
            return data['token']

        logger.error(f"Failed to get auth token: {data}")
        return None

    async def get_session_from_token(self, token: str) -> Optional[str]:
        """Exchange an authorized token for a session key."""
        sig_params = {
            'method': 'auth.getsession',
            'token': token,
            'api_key': self.api_key
        }

        params = {
            'method': 'auth.getsession',
            'token': token,
            'api_key': self.api_key,
            'api_sig': self._get_api_signature(sig_params),
            'format': 'json'
        }

        data = await self._request(params)

        if data and 'session' in data:
            session = data['session']
            if 'key' in session:
                return session['key']

        logger.error(f"Failed to get session from token: {data}")
        return None

    def get_auth_url(self, token: str) -> str:
        """Get the Last.fm authorization URL for a token."""
        return f"https://www.last.fm/api/auth/?api_key={self.api_key}&token={token}"
