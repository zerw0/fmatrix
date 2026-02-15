# FMatrix

A Matrix bot that shows your Last.fm stats and Discogs collection directly in your rooms. Check your top artists, see what you've been listening to, browse your vinyl collection, and compete with friends on leaderboards.

## Quick Start

Here's what you need:

**1. Last.fm API credentials**
   - Go to https://www.last.fm/api/account/create
   - Make an app and copy your API Key and Secret

**2. Discogs User Token (Optional)**
   - Go to https://www.discogs.com/settings/developers
   - Generate a new token for personal use
   - Copy the token for your `.env` file

**3. Matrix bot account**
   - Register a bot account on your homeserver (matrix.org works)
   - You'll need the full user ID like `@fmatrix:matrix.org` and password

**3. Setup**

```bash
git clone https://github.com/zerw0/fmatrix
cd fmatrix
cp .env.example .env
nano .env  # add your credentials
```

Your `.env` should look like:
```
MATRIX_HOMESERVER=https://matrix.org
MATRIX_USER_ID=@yourbot:matrix.org
MATRIX_PASSWORD=your_password
LASTFM_API_KEY=get_this_from_lastfm
LASTFM_API_SECRET=get_this_too
DISCOGS_USER_TOKEN=optional_discogs_token
```

**4. Run it**

With Docker:
```bash
docker-compose up -d
```

Or just Python:
```bash
pip install -r requirements.txt
python main.py
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

**Discogs (if configured):**
- `!dg link username` - Connect your Discogs account
- `!dg stats` - Show your collection and wantlist stats
- `!dg collection [page]` - Browse your collection (10 items per page)
- `!dg wantlist [page]` - Browse your wantlist (10 items per page)
- `!dg search query` - Search Discogs database
- `!dg artist name` - Get artist info
- `!dg release name` - Get release info
- `!dg help` - Show Discogs help

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
discogs_client.py   Talks to Discogs API
database.py         SQLite for caching and user links
config.py           Loads your .env settings
```

The bot stores two things in SQLite:
- Which Matrix users are linked to which Last.fm accounts
- Which Matrix users are linked to which Discogs accounts (if configured)
- Cached stats so we don't hammer Last.fm's API

## Config

Everything's in `.env`:

```bash
MATRIX_HOMESERVER=https://matrix.org        # Your Matrix server
MATRIX_USER_ID=@bot:matrix.org              # Bot account
MATRIX_PASSWORD=hunter2                      # Bot password
LASTFM_API_KEY=abc123                        # From Last.fm
LASTFM_API_SECRET=def456                     # Also from Last.fm
DISCOGS_USER_TOKEN=xyz789                    # Optional: From Discogs
COMMAND_PREFIX=!                             # Change if you want
LOG_LEVEL=INFO                               # DEBUG for more logs
DATA_DIR=/data                               # Where to save the database
```

## Notes

- The bot caches stats for a bit to avoid rate limits (Last.fm allows 1 request/sec)
- Artist search tries to show images when available (falls back to album art)
- Leaderboards only work with people in the same room
- All Last.fm data is public anyway, so no privacy concerns
- Discogs integration is optional - the bot works fine without it
- Discogs commands require a user token (free from Discogs settings)

## Credits

Inspired by [fmbot.xyz](https://fmbot.xyz/) (the Discord one). Built this because Matrix needed one too.
