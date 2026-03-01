"""
Spotify API client for searching tracks and getting Spotify links.
"""

import logging
from typing import Optional, Dict, List
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

logger = logging.getLogger(__name__)


class SpotifyClient:
    """Client for interacting with Spotify API."""

    def __init__(self, client_id: str, client_secret: str):
        """Initialize Spotify client with credentials."""
        self.client_id = client_id
        self.client_secret = client_secret
        self.client: Optional[spotipy.Spotify] = None

    def _get_client(self) -> spotipy.Spotify:
        """Get or create Spotify client."""
        if self.client is None:
            auth_manager = SpotifyClientCredentials(
                client_id=self.client_id,
                client_secret=self.client_secret
            )
            self.client = spotipy.Spotify(auth_manager=auth_manager)
        return self.client

    async def search_track(self, query: str, limit: int = 10) -> Optional[List[Dict]]:
        """Search for a track on Spotify.

        Args:
            query: Search query (can be "artist - track" or just track name)
            limit: Number of results to return (for fuzzy matching)

        Returns:
            List of matching tracks, or None if not found
        """
        try:
            client = self._get_client()
            results = client.search(q=query, type='track', limit=limit)

            if results and results.get('tracks') and results['tracks'].get('items'):
                tracks = results['tracks']['items']
                if tracks:
                    return tracks  # Return all results for fuzzy matching
            return None
        except Exception as e:
            logger.error(f"Error searching Spotify for '{query}': {e}")
            return None

    async def search_track_by_artist_and_name(self, artist: str, track_name: str, limit: int = 10) -> Optional[list]:
        """Search for a track by artist and track name.

        Args:
            artist: Artist name
            track_name: Track name
            limit: Number of results to return

        Returns:
            List of matching tracks, or None if not found
        """
        query = f"artist:{artist} track:{track_name}"
        return await self.search_track(query, limit)
