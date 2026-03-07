"""
Lyrics client using lrclib.net — a free, open, no-auth lyrics API.

Docs: https://lrclib.net/docs
"""

import logging
from typing import Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://lrclib.net/api"
USER_AGENT = "fmatrix/0.1.0 (https://github.com/zerw0/fmatrix)"


class LyricsClient:
    """Client for fetching lyrics from lrclib.net."""

    def __init__(self) -> None:
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        return self.session

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self.session and not self.session.closed:
            await self.session.close()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def get_lyrics(
        self,
        artist: str,
        track: str,
        album: Optional[str] = None,
        duration: Optional[int] = None,
    ) -> Optional[Dict[str, str]]:
        """Try the ``GET /api/get`` exact-match endpoint first, then fall
        back to ``GET /api/search`` if that misses.

        Returns a dict with keys ``plain``, ``synced``, ``artist``,
        ``track``, ``album`` — or *None* when nothing is found.
        """
        result = await self._get_exact(artist, track, album, duration)
        if result:
            return result
        return await self._search(artist, track)

    # ------------------------------------------------------------------
    # Private: exact match
    # ------------------------------------------------------------------

    async def _get_exact(
        self,
        artist: str,
        track: str,
        album: Optional[str] = None,
        duration: Optional[int] = None,
    ) -> Optional[Dict[str, str]]:
        """``GET /api/get`` — exact match by artist + track (+ optional
        album / duration)."""
        session = await self._get_session()

        params: Dict[str, str] = {
            "artist_name": artist,
            "track_name": track,
        }
        if album:
            params["album_name"] = album
        if duration is not None:
            params["duration"] = str(duration)

        try:
            async with session.get(
                f"{BASE_URL}/get",
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    logger.warning(
                        "lrclib exact-match returned HTTP %s for %s - %s",
                        resp.status,
                        artist,
                        track,
                    )
                    return None
                data = await resp.json()
                return self._parse_result(data)
        except Exception as e:
            logger.error("lrclib exact-match error: %s", e)
            return None

    # ------------------------------------------------------------------
    # Private: search fallback
    # ------------------------------------------------------------------

    async def _search(
        self,
        artist: str,
        track: str,
    ) -> Optional[Dict[str, str]]:
        """``GET /api/search`` — keyword search, picks the best result."""
        session = await self._get_session()

        query = f"{artist} {track}"
        try:
            async with session.get(
                f"{BASE_URL}/search",
                params={"q": query},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "lrclib search returned HTTP %s for '%s'",
                        resp.status,
                        query,
                    )
                    return None
                results = await resp.json()
                if not results:
                    return None

                # Pick the first result that actually has lyrics
                for item in results:
                    parsed = self._parse_result(item)
                    if parsed and (parsed.get("plain") or parsed.get("synced")):
                        return parsed

                # Nothing with lyrics — return first result anyway (may have
                # metadata but empty lyrics body)
                return self._parse_result(results[0])
        except Exception as e:
            logger.error("lrclib search error: %s", e)
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_result(data: Dict) -> Optional[Dict[str, str]]:
        """Normalise a single lrclib response object into our format."""
        if not data:
            return None

        plain = (data.get("plainLyrics") or "").strip()
        synced = (data.get("syncedLyrics") or "").strip()

        if not plain and not synced:
            return None

        return {
            "plain": plain,
            "synced": synced,
            "artist": data.get("artistName", ""),
            "track": data.get("trackName", ""),
            "album": data.get("albumName", ""),
        }
