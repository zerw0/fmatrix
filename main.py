#!/usr/bin/env python3
"""
FMatrix - Matrix Bot for Last.fm Stats and Leaderboards
"""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from bot import FMatrixBot

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('fmatrix.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


async def health_check_loop():
    """Periodically write health status to file for Docker healthcheck."""
    # Use /data in Docker, ./data locally
    health_dir = Path('/data') if Path('/data').exists() else Path('./data')
    health_dir.mkdir(exist_ok=True)
    health_file = health_dir / 'health'

    while True:
        try:
            health_file.write_text(f"{time.time():.0f}")
        except Exception as e:
            logger.error(f"Health check write failed: {e}")
        await asyncio.sleep(30)


def _parse_args():
    parser = argparse.ArgumentParser(description="FMatrix - Matrix Bot for Last.fm Stats")
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        metavar="FILE",
        help="Path to .env-format config file (values override environment variables)",
    )
    return parser.parse_args()


async def main(config_path=None):
    """Main entry point for the bot."""
    logger.info("Starting FMatrix bot...")

    # Create bot instance
    bot = FMatrixBot(config_path=config_path)

    # Start health check loop in background
    asyncio.create_task(health_check_loop())

    try:
        # Initialize database
        await bot.init_db()

        # Start the bot
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Bot interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


def run():
    """Entry point for the fmatrix console script."""
    args = _parse_args()
    asyncio.run(main(config_path=args.config))


if __name__ == "__main__":
    run()
