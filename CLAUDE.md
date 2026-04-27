# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
# Install deps
pip install -r requirements.txt

# Run with a config file (recommended for local dev)
python main.py --config .env
# or after pip install -e .
fmatrix --config .env

# Docker (production)
docker-compose up -d
```

Copy `.env.example` to `.env` and fill in credentials. Required: `MATRIX_PASSWORD`, `LASTFM_API_KEY`, `LASTFM_API_SECRET`. All others are optional.

There is no test suite and no linter configured.

## Architecture

The bot is a single long-running async process built on [matrix-nio](https://github.com/poljar/matrix-nio).

**Startup sequence** (`main.py` → `bot.py`):
1. `FMatrixBot.__init__` — constructs all clients (LastfmClient, DiscogsClient, SpotifyClient, LyricsClient) and the Config
2. `bot.run()` — initializes the DB, sets up the Matrix client, logs in, starts a background `cache_cleanup_loop`, then enters `sync_with_invite_handling`
3. The sync loop does an initial sync (to get the sync token and skip old events), *then* registers event callbacks, then polls with `client.sync(timeout=10000)` indefinitely

**Command handling** (`bot_commands/`):

`CommandHandler` is assembled from four mixins via multiple inheritance:

| Class | File | Role |
|---|---|---|
| `CommandHandlerBase` | `base.py` | State (`db`, `lastfm`, …), `PaginationManager`, `send_message`, `send_image`, `normalize_command/period`, `_get_target_user` |
| `CommandRouterMixin` | `router.py` | Parses raw message, normalises command alias → canonical name, dispatches to method, handles reaction pagination |
| `LastfmCommandsMixin` | `lastfm.py` | All Last.fm bot commands (stats, top artists/tracks/albums, who-knows, leaderboard, auth flow, …) |
| `DiscogsCommandsMixin` | `discogs.py` | All Discogs bot commands (collection, wantlist, search, artist, release) |

Command aliases are defined in `CommandHandlerBase.COMMAND_ALIASES` (e.g. `"fm"` → `"lastfm"`, `"s"` → `"stats"`). The router calls `normalize_command()` before dispatch.

**Pagination:** `PaginationManager` (in `base.py`) stores in-memory state keyed by Matrix event ID. The router's `handle_reaction` method looks up the event that was reacted to and calls the stored callback to re-render the page.

**External clients:**
- `LastfmClient` — hand-rolled aiohttp client against `ws.audioscrobbler.com/2.0`; has its own session (lazy-created in `get_session()`) and retry logic in `_request()`
- `DiscogsClient` — same pattern, aiohttp, Discogs REST API
- `SpotifyClient` — uses the `spotipy` library (sync), no aiohttp session
- `LyricsClient` — aiohttp, scrapes lyrics APIs

All clients expose `async def close()` and are shut down from `FMatrixBot.close()`.

**Database** (`database.py`): aiosqlite with WAL mode. Tables:
- `user_mappings` — Matrix user ID ↔ Last.fm username + session key; one-to-one enforced in `link_user()`
- `discogs_mappings` — Matrix user ID ↔ Discogs username
- `stats_cache` / `playcount_cache` — short-lived Last.fm API response cache
- `auth_tokens` — pending Last.fm OAuth tokens during the `!fm link` flow

**Config** (`config.py`): reads env vars; a `.env`-format file (passed via `--config` or `CONFIG_FILE`) overrides the env. No external config library — custom parser.

## Key patterns

- All command methods live on mixins; `CommandHandlerBase` has no command logic itself
- `_get_target_user(room, sender, client, args)` resolves the target Last.fm username — pass it `args` for optional `@user` override, or omit for the sender's own account
- `send_message` and `send_image` are the only ways commands should post to rooms
- The Matrix client's initial sync intentionally skips all callbacks to avoid replaying old events; callbacks are registered after the first sync completes
- Last.fm auth is a multi-step flow: `!fm link` stores an auth token in the DB, sends a DM with the authorize URL, then `!fm authcomplete` exchanges the token for a session key via `lastfm.get_session_from_token()`
