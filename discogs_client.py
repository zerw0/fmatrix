"""
Discogs API client
"""

import logging
import aiohttp
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://api.discogs.com"


class DiscogsClient:
    """Client for interacting with Discogs API."""

    def __init__(self, user_token: str):
        self.user_token = user_token
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

    async def _request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make a request to Discogs API."""
        if params is None:
            params = {}

        headers = {
            'User-Agent': 'FMatrixBot/1.0',
            'Authorization': f'Discogs token={self.user_token}'
        }

        session = await self.get_session()
        url = f"{BASE_URL}{endpoint}"

        try:
            async with session.get(url, params=params, headers=headers,
                                  timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 404:
                    logger.warning(f"Discogs API 404: {endpoint}")
                    return None
                else:
                    logger.error(f"Discogs API error: {resp.status} - {await resp.text()}")
                    return None
        except Exception as e:
            logger.error(f"Error calling Discogs API: {e}")
            return None

    async def get_user_identity(self) -> Optional[Dict]:
        """Get the authenticated user's identity."""
        data = await self._request('/oauth/identity')
        return data

    async def get_user_profile(self, username: str) -> Optional[Dict]:
        """Get a user's profile."""
        data = await self._request(f'/users/{username}')
        return data

    async def get_user_collection(self, username: str, page: int = 1, per_page: int = 50) -> Optional[Dict]:
        """Get a user's collection."""
        data = await self._request(
            f'/users/{username}/collection/folders/0/releases',
            params={'page': page, 'per_page': per_page}
        )
        return data

    async def get_user_collection_stats(self, username: str) -> Optional[Dict]:
        """Get statistics about a user's collection."""
        collection = await self.get_user_collection(username, per_page=1)
        if not collection:
            return None

        stats = {
            'username': username,
            'total_items': collection.get('pagination', {}).get('items', 0),
            'total_pages': collection.get('pagination', {}).get('pages', 0)
        }
        return stats

    async def get_user_wantlist(self, username: str, page: int = 1, per_page: int = 50) -> Optional[Dict]:
        """Get a user's wantlist."""
        data = await self._request(
            f'/users/{username}/wants',
            params={'page': page, 'per_page': per_page}
        )
        return data

    async def get_user_wantlist_stats(self, username: str) -> Optional[Dict]:
        """Get statistics about a user's wantlist."""
        wantlist = await self.get_user_wantlist(username, per_page=1)
        if not wantlist:
            return None

        stats = {
            'username': username,
            'total_wants': wantlist.get('pagination', {}).get('items', 0)
        }
        return stats

    async def search(self, query: str, search_type: str = None, page: int = 1, per_page: int = 50) -> Optional[Dict]:
        """Search Discogs database.

        Args:
            query: Search query string
            search_type: Optional type ('release', 'master', 'artist', 'label')
            page: Page number
            per_page: Results per page
        """
        params = {
            'q': query,
            'page': page,
            'per_page': per_page
        }

        if search_type:
            params['type'] = search_type

        data = await self._request('/database/search', params=params)
        return data

    async def search_artist(self, artist_name: str, limit: int = 10) -> List[Dict]:
        """Search for an artist."""
        data = await self.search(artist_name, search_type='artist', per_page=limit)

        if data and 'results' in data:
            return data['results']
        return []

    async def search_release(self, release_name: str, limit: int = 10) -> List[Dict]:
        """Search for a release (album)."""
        data = await self.search(release_name, search_type='release', per_page=limit)

        if data and 'results' in data:
            return data['results']
        return []

    async def get_release(self, release_id: int) -> Optional[Dict]:
        """Get detailed information about a specific release."""
        data = await self._request(f'/releases/{release_id}')
        return data

    async def get_master_release(self, master_id: int) -> Optional[Dict]:
        """Get detailed information about a master release."""
        data = await self._request(f'/masters/{master_id}')
        return data

    async def get_artist(self, artist_id: int) -> Optional[Dict]:
        """Get detailed information about an artist."""
        data = await self._request(f'/artists/{artist_id}')
        return data

    async def get_artist_releases(self, artist_id: int, page: int = 1, per_page: int = 50) -> Optional[Dict]:
        """Get all releases by an artist."""
        data = await self._request(
            f'/artists/{artist_id}/releases',
            params={'page': page, 'per_page': per_page, 'sort': 'year', 'sort_order': 'desc'}
        )
        return data

    async def get_label(self, label_id: int) -> Optional[Dict]:
        """Get detailed information about a label."""
        data = await self._request(f'/labels/{label_id}')
        return data

    async def get_collection_value(self, username: str) -> Optional[Dict]:
        """Get the total value of a user's collection."""
        collection = await self.get_user_collection(username, per_page=100)

        if not collection or 'releases' not in collection:
            return None

        total_min = 0
        total_median = 0
        total_max = 0
        total_items = collection.get('pagination', {}).get('items', 0)

        # Sample first page only for quick stats
        for item in collection.get('releases', []):
            instance_value = item.get('basic_information', {}).get('value', {})
            if isinstance(instance_value, dict):
                total_min += instance_value.get('minimum', 0) or 0
                total_median += instance_value.get('median', 0) or 0
                total_max += instance_value.get('maximum', 0) or 0

        return {
            'username': username,
            'total_items': total_items,
            'sample_size': len(collection.get('releases', [])),
            'estimated_min_value': total_min,
            'estimated_median_value': total_median,
            'estimated_max_value': total_max,
            'currency': 'USD'
        }
