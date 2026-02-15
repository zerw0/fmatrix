# FMatrix

A Matrix bot that shows your Last.fm stats directly in your rooms. Check your top artists, see what you've been listening to, and compete with friends on leaderboards.

## Quick Start

Here's what you need:

**1. Last.fm API credentials**
   - Go to https://www.last.fm/api/account/create
   - Make an app and copy your API Key and Secret

**2. Matrix bot account**
   - Register a bot account on your homeserver (matrix.org works)
   - You'll need the full user ID like `@fmatrix:matrix.org` and password

**3. Setup**

Either use a config file (same format as `.env.example`) or set environment variables.

**Option A – config file**
```bash
git clone https://github.com/zerw0/fmatrix
cd fmatrix
cp .env.example .env
nano .env  # add your credentials
```

**Option B – environment variables**  
Export `MATRIX_*`, `LASTFM_*`, etc. (see Config section below).

**4. Run it**

With Docker:
```bash
docker-compose up -d
```
(Uses `.env` via `env_file`; see docker-compose section.)

Or with Python using a config file:
```bash
pip install -r requirements.txt
fmatrix --config .env
# or: python main.py --config .env
```

Or with Python using only environment variables:
```bash
export MATRIX_PASSWORD=… LASTFM_API_KEY=…  # etc.
fmatrix
```

**5. Use it**
   - Invite the bot to a room
   - Link your Last.fm: `!fm link your_username`
   - Try it out: `!fm s`

## Commands

All commands start with `!fm` (or use shortcuts). Here's what works:

**Your stats:**
- `!fm stats` or `!s` - Your listening overview (scrobbles, artists, tracks, albums)
- `!fm recent` or `!r` - Last 7 tracks you listened to
- `!fm tar [period]` - Top artists
- `!fm ta [period]` - Top albums
- `!fm tt [period]` - Top tracks

**Who knows:**
- `!fm wk Arctic Monkeys` - Who in this room knows Arctic Monkeys?
- `!fm wkt Song Name` - Who in this room knows this track?
- `!fm wka Album Name` - Who in this room knows this album?

**Room stuff:**
- `!fm lb` - Leaderboard by scrobbles
- `!fm lb artistcount` - Who has the most unique artists
- `!fm lb trackcount` - Who has the most unique tracks

**Setup:**
- `!fm link username` or `!l username` - Connect your Last.fm
- `!fm help` or `!?` - Show all commands

**Time periods:** (add to any stat command)
```
7d, 7day, 7days     → Last week
1m, 1month          → Last month
3m, 3month          → Last 3 months
6m, 6month          → Last 6 months
12m, 12month        → Last year
overall, 1y, all    → All time (default)
```

**Examples:**
```
!ta 7d              Top albums from this week
!tt 1m              Top tracks from this month
!s                  Your stats
!wk Beyoncé         Who in this room knows Beyoncé? (shows image if available)
!lb                 Who in this room has the most scrobbles?
```

## What's Inside

```
main.py              Starts everything
bot.py              Matrix client stuff
commands.py         All the command logic
lastfm_client.py    Talks to Last.fm's API
database.py         SQLite for caching and user links
config.py           Reads env vars and optional .env-format config file
```

The bot stores two things in SQLite:
- Which Matrix users are linked to which Last.fm accounts
- Cached stats so we don't hammer Last.fm's API

## Config

Configuration is read from **environment variables**. You can optionally use an **.env-format config file** (see `.env.example`); values in the file override the same options set in the environment.

**Ways to supply config:**
- **Config file:** `fmatrix --config /path/to/config.env` or set `CONFIG_FILE=/path/to/config.env`
- **Environment:** export the variables (e.g. in your shell or via Docker `env_file` / `environment`)

**Options** (same keys in env or in the config file):

```bash
MATRIX_HOMESERVER=https://matrix.org        # Your Matrix server
MATRIX_USER_ID=@bot:matrix.org              # Bot account
MATRIX_PASSWORD=hunter2                      # Bot password
LASTFM_API_KEY=abc123                        # From Last.fm
LASTFM_API_SECRET=def456                     # Also from Last.fm
COMMAND_PREFIX=!                             # Change if you want
LOG_LEVEL=INFO                               # DEBUG for more logs
DATA_DIR=/data                               # Where to save the database
AUTO_JOIN_ROOMS=                             # Comma-separated room IDs/aliases to auto-join
```

## Notes

- The bot caches stats for a bit to avoid rate limits (Last.fm allows 1 request/sec)
- Artist search tries to show images when available (falls back to album art)
- Leaderboards only work with people in the same room
- All Last.fm data is public anyway, so no privacy concerns

## Credits

Inspired by [fmbot.xyz](https://fmbot.xyz/) (the Discord one). Built this because Matrix needed one too.
