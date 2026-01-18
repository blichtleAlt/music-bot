import asyncio
import logging
import os
import random
from collections import deque
from datetime import datetime

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp

load_dotenv()

# Set up verbose logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)-20s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("music-bot")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Per-guild music queues and state
queues: dict[int, deque] = {}
current_track: dict[int, dict] = {}

# Autoplay state per guild
autoplay_state: dict[int, dict] = {}
# Structure: {guild_id: {"artist": str, "start_time": datetime, "played_titles": set, "task": asyncio.Task}}

# yt-dlp options
YDL_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "extract_flat": False,
}

# FFmpeg options for Discord streaming
FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


def get_queue(guild_id: int) -> deque:
    if guild_id not in queues:
        queues[guild_id] = deque()
    return queues[guild_id]


async def extract_info(query: str) -> dict:
    """Extract video info using yt-dlp. Supports URLs or search queries."""
    loop = asyncio.get_event_loop()

    def _extract():
        # If it's not a URL, treat it as a YouTube search
        search_query = query
        if not query.startswith(("http://", "https://")):
            search_query = f"ytsearch:{query}"

        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            info = ydl.extract_info(search_query, download=False)

            # Handle search results (returns a playlist with entries)
            if "entries" in info:
                info = info["entries"][0]

            return {
                "url": info["url"],
                "title": info.get("title", "Unknown"),
                "duration": info.get("duration", 0),
                "webpage_url": info.get("webpage_url", query),
            }

    return await loop.run_in_executor(None, _extract)


async def search_artist_songs(artist: str, max_results: int = 20) -> list[dict]:
    """Search YouTube for songs by an artist. Returns a list of track info."""
    loop = asyncio.get_event_loop()

    def _search():
        search_query = f"ytsearch{max_results}:{artist} official audio"
        opts = {**YDL_OPTS, "extract_flat": True}

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(search_query, download=False)
            results = []

            for entry in info.get("entries", []):
                if entry:
                    results.append({
                        "id": entry.get("id"),
                        "title": entry.get("title", "Unknown"),
                        "url": entry.get("url"),
                        "webpage_url": f"https://www.youtube.com/watch?v={entry.get('id')}",
                    })

            return results

    return await loop.run_in_executor(None, _search)


async def get_full_track_info(video_id: str) -> dict:
    """Get full track info including stream URL for a video ID."""
    loop = asyncio.get_event_loop()

    def _extract():
        url = f"https://www.youtube.com/watch?v={video_id}"
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "url": info["url"],
                "title": info.get("title", "Unknown"),
                "duration": info.get("duration", 0),
                "webpage_url": info.get("webpage_url", url),
            }

    return await loop.run_in_executor(None, _extract)


def play_next(guild_id: int, voice_client: discord.VoiceClient):
    """Play the next track in the queue."""
    queue = get_queue(guild_id)

    if not queue:
        current_track.pop(guild_id, None)
        return

    track = queue.popleft()
    current_track[guild_id] = track

    source = discord.FFmpegPCMAudio(track["url"], **FFMPEG_OPTS)

    def after_playing(error):
        if error:
            print(f"Player error: {error}")
        # Schedule next track
        asyncio.run_coroutine_threadsafe(
            play_next_async(guild_id, voice_client),
            bot.loop,
        )

    voice_client.play(source, after=after_playing)


async def play_next_async(guild_id: int, voice_client: discord.VoiceClient):
    """Async wrapper for play_next."""
    if voice_client and voice_client.is_connected():
        play_next(guild_id, voice_client)


@bot.event
async def on_ready():
    logger.info(f"Bot is ready! Logged in as {bot.user}")
    logger.info(f"Bot ID: {bot.user.id}")
    logger.info(f"Connected to {len(bot.guilds)} guild(s):")
    for guild in bot.guilds:
        logger.info(f"  - {guild.name} (ID: {guild.id})")


@bot.event
async def on_message(message: discord.Message):
    """Log all incoming messages for debugging."""
    # Ignore bot's own messages
    if message.author.bot:
        return

    logger.debug(
        f"MESSAGE RECEIVED: Guild={message.guild.name if message.guild else 'DM'} | "
        f"Channel=#{message.channel.name if hasattr(message.channel, 'name') else 'DM'} | "
        f"Author={message.author} | Content={message.content!r}"
    )

    # Check if it looks like a command
    if message.content.startswith("!"):
        logger.info(f"COMMAND DETECTED: {message.content}")

    # IMPORTANT: Process commands after logging
    await bot.process_commands(message)


@bot.event
async def on_command(ctx: commands.Context):
    """Log when a command is invoked."""
    logger.info(f"COMMAND INVOKED: {ctx.command.name} by {ctx.author} in #{ctx.channel.name}")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    """Log command errors."""
    logger.error(f"COMMAND ERROR: {ctx.command.name if ctx.command else 'Unknown'} - {error}")
    if isinstance(error, commands.CommandNotFound):
        logger.warning(f"Command not found: {ctx.message.content}")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: {error.param.name}")
    else:
        await ctx.send(f"Error: {error}")


@bot.command(name="ping")
async def ping(ctx: commands.Context):
    """Simple test command to check if bot is responding."""
    logger.info("Ping command executed!")
    await ctx.send(f"Pong! Latency: {round(bot.latency * 1000)}ms")


@bot.command(name="join")
async def join(ctx: commands.Context):
    """Join the user's voice channel."""
    if not ctx.author.voice:
        await ctx.send("You're not in a voice channel!")
        return

    channel = ctx.author.voice.channel

    if ctx.voice_client:
        await ctx.voice_client.move_to(channel)
    else:
        await channel.connect()

    await ctx.send(f"Joined **{channel.name}**")


@bot.command(name="leave")
async def leave(ctx: commands.Context):
    """Leave the voice channel."""
    if not ctx.voice_client:
        await ctx.send("I'm not in a voice channel!")
        return

    # Clear queue and state
    guild_id = ctx.guild.id
    queues.pop(guild_id, None)
    current_track.pop(guild_id, None)

    await ctx.voice_client.disconnect()
    await ctx.send("Left the voice channel")


@bot.command(name="play", aliases=["p"])
async def play(ctx: commands.Context, *, url: str):
    """Play a YouTube URL or add it to the queue."""
    # Auto-join if not in a channel
    if not ctx.voice_client:
        if not ctx.author.voice:
            await ctx.send("You're not in a voice channel!")
            return
        await ctx.author.voice.channel.connect()

    # Wait for voice connection to be ready
    for _ in range(20):  # Wait up to 10 seconds
        if ctx.voice_client.is_connected():
            break
        await asyncio.sleep(0.5)
    else:
        await ctx.send("Failed to connect to voice channel. Try again.")
        return

    await ctx.send(f"Fetching: `{url}`...")

    try:
        track = await extract_info(url)
    except Exception as e:
        await ctx.send(f"Error fetching video: {e}")
        return

    guild_id = ctx.guild.id
    queue = get_queue(guild_id)

    # If already playing, add to queue
    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        queue.append(track)
        await ctx.send(f"Added to queue: **{track['title']}** (position {len(queue)})")
    else:
        # Play immediately
        queue.append(track)
        play_next(guild_id, ctx.voice_client)
        await ctx.send(f"Now playing: **{track['title']}**")


@bot.command(name="pause")
async def pause(ctx: commands.Context):
    """Pause playback."""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Paused")
    else:
        await ctx.send("Nothing is playing")


@bot.command(name="resume")
async def resume(ctx: commands.Context):
    """Resume playback."""
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("Resumed")
    else:
        await ctx.send("Nothing is paused")


@bot.command(name="skip", aliases=["s"])
async def skip(ctx: commands.Context):
    """Skip the current track."""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()  # This triggers play_next via the after callback
        await ctx.send("Skipped")
    else:
        await ctx.send("Nothing is playing")


@bot.command(name="stop")
async def stop(ctx: commands.Context):
    """Stop playback and clear the queue."""
    guild_id = ctx.guild.id

    queues.pop(guild_id, None)
    current_track.pop(guild_id, None)

    if ctx.voice_client:
        ctx.voice_client.stop()

    await ctx.send("Stopped and cleared queue")


@bot.command(name="queue", aliases=["q"])
async def show_queue(ctx: commands.Context):
    """Show the current queue."""
    guild_id = ctx.guild.id
    queue = get_queue(guild_id)
    now = current_track.get(guild_id)

    if not now and not queue:
        await ctx.send("Queue is empty")
        return

    lines = []
    if now:
        lines.append(f"**Now playing:** {now['title']}")

    if queue:
        lines.append(f"\n**Up next ({len(queue)} tracks):**")
        for i, track in enumerate(list(queue)[:10], 1):
            lines.append(f"{i}. {track['title']}")
        if len(queue) > 10:
            lines.append(f"...and {len(queue) - 10} more")

    await ctx.send("\n".join(lines))


@bot.command(name="np", aliases=["nowplaying"])
async def now_playing(ctx: commands.Context):
    """Show the currently playing track."""
    guild_id = ctx.guild.id
    now = current_track.get(guild_id)

    if now:
        await ctx.send(f"Now playing: **{now['title']}**")
    else:
        await ctx.send("Nothing is playing")


@bot.command(name="clear")
async def clear_queue(ctx: commands.Context):
    """Clear the queue (keeps current track playing)."""
    guild_id = ctx.guild.id
    queue = get_queue(guild_id)
    queue.clear()
    await ctx.send("Queue cleared")


# Autoplay duration in seconds (2 hours)
AUTOPLAY_DURATION = 2 * 60 * 60


async def autoplay_loop(guild_id: int, voice_client: discord.VoiceClient, channel: discord.TextChannel):
    """Background task that keeps adding songs to the queue during autoplay."""
    state = autoplay_state.get(guild_id)
    if not state:
        return

    artist = state["artist"]
    logger.info(f"Autoplay started for artist: {artist} in guild {guild_id}")

    try:
        while True:
            # Check if autoplay should stop (2 hour limit)
            elapsed = (datetime.now() - state["start_time"]).total_seconds()
            if elapsed >= AUTOPLAY_DURATION:
                await channel.send(f"Autoplay ended after 2 hours. Played {len(state['played_titles'])} unique songs.")
                break

            # Check if still connected
            if not voice_client or not voice_client.is_connected():
                logger.info("Voice client disconnected, stopping autoplay")
                break

            # Check queue size - add more songs if queue is getting low
            queue = get_queue(guild_id)
            if len(queue) < 3:
                logger.debug(f"Queue low ({len(queue)} tracks), fetching more songs for {artist}")

                # Search for more songs
                try:
                    songs = await search_artist_songs(artist, max_results=30)
                except Exception as e:
                    logger.error(f"Error searching for songs: {e}")
                    await asyncio.sleep(10)
                    continue

                # Filter out already played songs
                new_songs = [s for s in songs if s["title"] not in state["played_titles"]]

                if not new_songs:
                    # All songs played, do a different search
                    logger.info(f"All known songs played for {artist}, trying different search")
                    try:
                        songs = await search_artist_songs(f"{artist} songs", max_results=30)
                        new_songs = [s for s in songs if s["title"] not in state["played_titles"]]
                    except Exception as e:
                        logger.error(f"Error in fallback search: {e}")

                if not new_songs:
                    await channel.send(f"Ran out of new songs for **{artist}**. Stopping autoplay.")
                    break

                # Add a few songs to queue (randomize order for variety)
                random.shuffle(new_songs)
                songs_to_add = new_songs[:5]

                for song in songs_to_add:
                    try:
                        track = await get_full_track_info(song["id"])
                        state["played_titles"].add(track["title"])
                        queue.append(track)
                        logger.debug(f"Added to queue: {track['title']}")
                    except Exception as e:
                        logger.error(f"Error getting track info for {song['title']}: {e}")
                        continue

                # Start playing if nothing is playing
                if not voice_client.is_playing() and not voice_client.is_paused() and queue:
                    play_next(guild_id, voice_client)

            # Wait before checking again
            await asyncio.sleep(5)

    except asyncio.CancelledError:
        logger.info(f"Autoplay task cancelled for guild {guild_id}")
    except Exception as e:
        logger.error(f"Autoplay error: {e}")
        await channel.send(f"Autoplay error: {e}")
    finally:
        autoplay_state.pop(guild_id, None)
        logger.info(f"Autoplay ended for guild {guild_id}")


@bot.command(name="autoplay", aliases=["ap"])
async def autoplay(ctx: commands.Context, *, artist: str):
    """Start autoplay mode for an artist. Plays songs for 2 hours with no duplicates.
    Usage: !autoplay <artist name>
    """
    guild_id = ctx.guild.id

    # Stop existing autoplay if running
    if guild_id in autoplay_state:
        old_task = autoplay_state[guild_id].get("task")
        if old_task:
            old_task.cancel()
        autoplay_state.pop(guild_id, None)

    # Auto-join if not in a channel
    if not ctx.voice_client:
        if not ctx.author.voice:
            await ctx.send("You're not in a voice channel!")
            return
        await ctx.author.voice.channel.connect()

    # Wait for voice connection
    for _ in range(20):
        if ctx.voice_client.is_connected():
            break
        await asyncio.sleep(0.5)
    else:
        await ctx.send("Failed to connect to voice channel. Try again.")
        return

    await ctx.send(f"Starting autoplay for **{artist}** (2 hours, no duplicates)...")

    # Initialize autoplay state
    autoplay_state[guild_id] = {
        "artist": artist,
        "start_time": datetime.now(),
        "played_titles": set(),
        "task": None,
    }

    # Start the autoplay background task
    task = asyncio.create_task(autoplay_loop(guild_id, ctx.voice_client, ctx.channel))
    autoplay_state[guild_id]["task"] = task


@bot.command(name="stopautoplay", aliases=["sap"])
async def stop_autoplay(ctx: commands.Context):
    """Stop autoplay mode."""
    guild_id = ctx.guild.id

    if guild_id not in autoplay_state:
        await ctx.send("Autoplay is not running.")
        return

    state = autoplay_state[guild_id]
    task = state.get("task")
    if task:
        task.cancel()

    played_count = len(state.get("played_titles", set()))
    autoplay_state.pop(guild_id, None)

    await ctx.send(f"Autoplay stopped. Played {played_count} unique songs.")


@bot.command(name="autoplaystatus", aliases=["aps"])
async def autoplay_status(ctx: commands.Context):
    """Check autoplay status."""
    guild_id = ctx.guild.id

    if guild_id not in autoplay_state:
        await ctx.send("Autoplay is not running.")
        return

    state = autoplay_state[guild_id]
    artist = state["artist"]
    elapsed = (datetime.now() - state["start_time"]).total_seconds()
    remaining = max(0, AUTOPLAY_DURATION - elapsed)
    played_count = len(state["played_titles"])

    mins_remaining = int(remaining // 60)
    await ctx.send(
        f"Autoplay: **{artist}**\n"
        f"Songs played: {played_count}\n"
        f"Time remaining: {mins_remaining} minutes"
    )


# ESPN API endpoints
ESPN_ENDPOINTS = {
    "NFL": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "NBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "NHL": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard",
}

# Team emojis - DEMON MODE 😈
NFL_EMOJIS = {
    "ARI": "🐦", "ATL": "🦅", "BAL": "🐦‍⬛", "BUF": "🦬",
    "CAR": "🐆", "CHI": "🐻", "CIN": "🐅", "CLE": "🟤",
    "DAL": "⭐", "DEN": "🐴", "DET": "🦁", "GB": "🧀",
    "HOU": "🤠", "IND": "🐴", "JAX": "🐆", "KC": "🪶",
    "LV": "☠️", "LAC": "⚡", "LAR": "🐏", "MIA": "🐬",
    "MIN": "⚔️", "NE": "🇺🇸", "NO": "⚜️", "NYG": "🗽",
    "NYJ": "✈️", "PHI": "🦅", "PIT": "🔩", "SF": "⛏️",
    "SEA": "🦚", "TB": "🏴‍☠️", "TEN": "⚔️", "WAS": "🎖️",
}

NBA_EMOJIS = {
    "ATL": "🦅", "BOS": "☘️", "BKN": "🗽", "CHA": "🐝",
    "CHI": "🐂", "CLE": "⚔️", "DAL": "🐴", "DEN": "⛏️",
    "DET": "🔧", "GS": "⚔️", "GSW": "⚔️", "HOU": "🚀",
    "IND": "🏎️", "LAC": "⛵", "LAL": "💜", "MEM": "🐻",
    "MIA": "🔥", "MIL": "🦌", "MIN": "🐺", "NOP": "🦅",
    "NY": "🗽", "NYK": "🗽", "OKC": "⛈️", "ORL": "✨",
    "PHI": "🔔", "PHX": "☀️", "POR": "🌲", "SAC": "👑",
    "SA": "🤠", "SAS": "🤠", "TOR": "🦖", "UTA": "🎷",
    "UTAH": "🎷", "WAS": "🧙",
}

NHL_EMOJIS = {
    "ANA": "🦆", "ARI": "🐺", "BOS": "🐻", "BUF": "⚔️",
    "CGY": "🔥", "CAR": "🌀", "CHI": "🪶", "COL": "⛰️",
    "CBJ": "🎖️", "DAL": "⭐", "DET": "🐙", "EDM": "🛢️",
    "FLA": "🐆", "LA": "👑", "LAK": "👑", "MIN": "🌲",
    "MTL": "🔵", "NSH": "🎸", "NJ": "😈", "NJD": "😈",
    "NYI": "🏝️", "NYR": "🗽", "OTT": "🏛️", "PHI": "🟠",
    "PIT": "🐧", "SJ": "🦈", "SJS": "🦈", "SEA": "🦑",
    "STL": "🎵", "TB": "⚡", "TBL": "⚡", "TOR": "🍁",
    "UTA": "🏔️", "VAN": "🐋", "VGK": "⚔️", "WSH": "🦅",
    "WPG": "✈️",
}

LEAGUE_EMOJIS = {"NFL": NFL_EMOJIS, "NBA": NBA_EMOJIS, "NHL": NHL_EMOJIS}


async def fetch_scores(league: str) -> list[dict]:
    """Fetch scores from ESPN API for a given league (today + tomorrow)."""
    url = ESPN_ENDPOINTS.get(league)
    if not url:
        return []

    # Get today and tomorrow's dates
    from datetime import timedelta
    today = datetime.now()
    tomorrow = today + timedelta(days=1)
    dates = [today.strftime("%Y%m%d"), tomorrow.strftime("%Y%m%d")]

    games = []
    async with aiohttp.ClientSession() as session:
        for date in dates:
            async with session.get(f"{url}?dates={date}") as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()

                for event in data.get("events", []):
                    competition = event.get("competitions", [{}])[0]
                    competitors = competition.get("competitors", [])

                    if len(competitors) < 2:
                        continue

                    # Get team info (away is usually first, home second in ESPN API)
                    away = competitors[0] if competitors[0].get("homeAway") == "away" else competitors[1]
                    home = competitors[1] if competitors[1].get("homeAway") == "home" else competitors[0]

                    status = event.get("status", {})
                    state = status.get("type", {}).get("state", "")  # pre, in, post
                    detail = status.get("type", {}).get("shortDetail", "")

                    game = {
                        "away_team": away.get("team", {}).get("abbreviation", "???"),
                        "away_score": away.get("score", "0"),
                        "home_team": home.get("team", {}).get("abbreviation", "???"),
                        "home_score": home.get("score", "0"),
                        "state": state,
                        "detail": detail,
                        "name": event.get("shortName", ""),
                        "league": league,
                    }
                    games.append(game)

    return games


def get_team_emoji(team: str, league: str) -> str:
    """Get emoji for a team."""
    emojis = LEAGUE_EMOJIS.get(league, {})
    return emojis.get(team, "")


def format_game_live(game: dict) -> str:
    """Format a live game - DEMON MODE."""
    away = game["away_team"]
    home = game["home_team"]
    league = game["league"]
    away_score = int(game["away_score"])
    home_score = int(game["home_score"])
    detail = game["detail"]

    away_emoji = get_team_emoji(away, league)
    home_emoji = get_team_emoji(home, league)

    # Determine who's winning
    if away_score > home_score:
        away_display = f"**{away_emoji} {away} {away_score}**"
        home_display = f"{home_emoji} {home} {home_score}"
    elif home_score > away_score:
        away_display = f"{away_emoji} {away} {away_score}"
        home_display = f"**{home_emoji} {home} {home_score}**"
    else:
        away_display = f"{away_emoji} {away} {away_score}"
        home_display = f"{home_emoji} {home} {home_score}"

    return f"🔴 `LIVE` │ {away_display}  @  {home_display} │ `{detail}`"


def format_game_final(game: dict) -> str:
    """Format a finished game - DEMON MODE."""
    away = game["away_team"]
    home = game["home_team"]
    league = game["league"]
    away_score = int(game["away_score"])
    home_score = int(game["home_score"])

    away_emoji = get_team_emoji(away, league)
    home_emoji = get_team_emoji(home, league)

    # Winner gets the crown
    if away_score > home_score:
        away_display = f"👑 {away_emoji} {away} {away_score}"
        home_display = f"{home_emoji} {home} {home_score}"
    elif home_score > away_score:
        away_display = f"{away_emoji} {away} {away_score}"
        home_display = f"👑 {home_emoji} {home} {home_score}"
    else:
        away_display = f"{away_emoji} {away} {away_score}"
        home_display = f"{home_emoji} {home} {home_score}"

    return f"✅ `FINAL` │ {away_display}  @  {home_display}"


def format_game_scheduled(game: dict) -> str:
    """Format a scheduled game - DEMON MODE."""
    away = game["away_team"]
    home = game["home_team"]
    league = game["league"]
    detail = game["detail"]

    away_emoji = get_team_emoji(away, league)
    home_emoji = get_team_emoji(home, league)

    return f"⏰ `{detail}` │ {away_emoji} {away}  @  {home_emoji} {home}"


@bot.command(name="cs", aliases=["scores"])
async def current_scores(ctx: commands.Context, league: str = None):
    """Show current sports scores. Usage: !cs [nfl|nba|nhl] or !cs for all."""
    leagues = ["NFL", "NBA", "NHL"]

    if league:
        league = league.upper()
        if league not in leagues:
            await ctx.send(f"Unknown league. Use: {', '.join(leagues)}")
            return
        leagues = [league]

    league_icons = {"NFL": "🏈", "NBA": "🏀", "NHL": "🏒"}
    output = []

    for lg in leagues:
        games = await fetch_scores(lg)
        icon = league_icons.get(lg, "🎮")

        if not games:
            output.append(f"{icon} **{lg}** │ No games today")
            continue

        # Separate by state
        live = [g for g in games if g["state"] == "in"]
        finished = [g for g in games if g["state"] == "post"]
        scheduled = [g for g in games if g["state"] == "pre"]

        # Header
        output.append(f"{icon} **━━━ {lg} SCOREBOARD ━━━** {icon}")

        # Live games first - these are the hottest
        if live:
            output.append("🔥 **LIVE**")
            output.extend(format_game_live(g) for g in live)

        # Final scores
        if finished:
            output.append("📊 **FINAL**")
            output.extend(format_game_final(g) for g in finished)

        # Upcoming
        if scheduled:
            output.append("📅 **UPCOMING**")
            output.extend(format_game_scheduled(g) for g in scheduled)

        output.append("")  # Blank line between leagues

    await ctx.send("\n".join(output).strip())


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN not set in .env file")
        exit(1)
    bot.run(token)
