"""
Configuration management for the bot.
Reads from environment variables; an optional .env-format config file
(see .env.example) can override env vars. Config file path can be set via
CONFIG_FILE or the --config command line argument.
"""

import os
from pathlib import Path


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a .env-style file (KEY=VALUE per line). Returns dict of key -> value."""
    result = {}
    if not path.exists():
        return result
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key:
                result[key] = value
    return result


def _get(key: str, file_config: dict[str, str], default: str | None = None) -> str | None:
    """Get config value: file overrides env, then default."""
    if key in file_config and file_config[key] != "":
        return file_config[key]
    return os.getenv(key, default)


class Config:
    """Bot configuration."""

    def __init__(self, config_path: Path | str | None = None):
        path = None
        if config_path is not None:
            path = Path(config_path)
        elif os.getenv("CONFIG_FILE"):
            path = Path(os.getenv("CONFIG_FILE"))
        file_config = _load_env_file(path) if path else {}

        # Matrix Configuration
        self.matrix_homeserver = _get("MATRIX_HOMESERVER", file_config, "https://matrix.org")
        self.matrix_user_id = _get("MATRIX_USER_ID", file_config, "@fmbot:matrix.org")
        self.matrix_password = _get("MATRIX_PASSWORD", file_config)
        self.matrix_device_id = _get("MATRIX_DEVICE_ID", file_config, "FMBOT001")

        # Last.fm Configuration
        self.lastfm_api_key = _get("LASTFM_API_KEY", file_config)
        self.lastfm_api_secret = _get("LASTFM_API_SECRET", file_config)

        # Discogs Configuration
        self.discogs_user_token = os.getenv('DISCOGS_USER_TOKEN')

        # Bot Configuration
        self.command_prefix = _get("COMMAND_PREFIX", file_config, "!")
        self.log_level = _get("LOG_LEVEL", file_config, "INFO")

        # Room Configuration - comma-separated list of room IDs or aliases to join
        rooms_str = _get("AUTO_JOIN_ROOMS", file_config) or ""
        self.auto_join_rooms = [room.strip() for room in rooms_str.split(",") if room.strip()]

        # Database Configuration
        data_dir = Path(_get("DATA_DIR", file_config, "./data"))
        data_dir.mkdir(exist_ok=True)
        self.db_path = str(data_dir / "fmatrix.db")

        # Validate required configuration
        if not self.matrix_password:
            raise ValueError("MATRIX_PASSWORD is required (set in environment or config file)")
        if not self.lastfm_api_key:
            raise ValueError("LASTFM_API_KEY is required (set in environment or config file)")
