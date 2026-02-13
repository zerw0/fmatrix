#!/usr/bin/env python3
"""
FMatrix - Matrix Bot for Last.fm Stats and Leaderboards
"""

import asyncio
import logging
import sys
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


async def main():
    """Main entry point for the bot."""
    logger.info("Starting FMatrix bot...")

    # Create bot instance
    bot = FMatrixBot()

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


if __name__ == "__main__":
    asyncio.run(main())
