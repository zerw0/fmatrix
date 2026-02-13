"""
Configuration management for the bot
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Config:
    """Bot configuration."""

    def __init__(self):
        # Matrix Configuration
        self.matrix_homeserver = os.getenv(
            'MATRIX_HOMESERVER',
            'https://matrix.org'
        )
        self.matrix_user_id = os.getenv(
            'MATRIX_USER_ID',
            '@fmbot:matrix.org'
        )
        self.matrix_password = os.getenv('MATRIX_PASSWORD')
        self.matrix_device_id = os.getenv('MATRIX_DEVICE_ID', 'FMBOT001')

        # Last.fm Configuration
        self.lastfm_api_key = os.getenv('LASTFM_API_KEY')
        self.lastfm_api_secret = os.getenv('LASTFM_API_SECRET')

        # Bot Configuration
        self.command_prefix = os.getenv('COMMAND_PREFIX', '!')
        self.log_level = os.getenv('LOG_LEVEL', 'INFO')

        # Room Configuration - comma-separated list of room IDs or aliases to join
        rooms_str = os.getenv('AUTO_JOIN_ROOMS', '')
        self.auto_join_rooms = [room.strip() for room in rooms_str.split(',') if room.strip()]

        # Database Configuration
        data_dir = Path(os.getenv('DATA_DIR', './data'))
        data_dir.mkdir(exist_ok=True)
        self.db_path = str(data_dir / 'fmatrix.db')

        # Validate required configuration
        if not self.matrix_password:
            raise ValueError("MATRIX_PASSWORD environment variable is required")
        if not self.lastfm_api_key:
            raise ValueError("LASTFM_API_KEY environment variable is required")
