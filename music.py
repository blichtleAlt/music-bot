import asyncio
import json
import logging
import os
import random
import re
import tempfile
from collections import deque
from datetime import datetime, timedelta

import discord
import edge_tts
from discord import ui
from discord.ext import commands
import yt_dlp

from bot import cleanup, MessageCleanup

# Station persistence file
STATIONS_FILE = "stations.json"


def load_stations() -> dict:
    """Load saved stations from JSON file."""
    if os.path.exists(STATIONS_FILE):
        try:
            with open(STATIONS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_stations(data: dict):
    """Save stations to JSON file."""
    with open(STATIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


# Energy level modifiers for radio searches
ENERGY_MODIFIERS = {
    -2: "slow ambient calm relaxing",
    -1: "chill mellow laid back",
    0: "",
    1: "upbeat energetic",
    2: "hype intense bangers high energy",
}

# Patterns that indicate a video is likely NOT a song
NON_SONG_PATTERNS = [
    r"\binterview\b",
    r"\bpodcast\b",
    r"\breaction\b",
    r"\breview\b",
    r"\bfull album\b",
    r"\bcomplete album\b",
    r"\blive stream\b",
    r"\blivestream\b",
    r"\bmaking of\b",
    r"\bbehind the scenes\b",
    r"\bdocumentary\b",
    r"\btutorial\b",
    r"\blesson\b",
    r"\bhow to\b",
    r"\bcompilation\b",
    r"\bmix 20\d\d\b",  # "mix 2024" type compilations
    r"\b1 hour\b",
    r"\b2 hour\b",
    r"\b3 hour\b",
    r"\bplaylist\b",
    r"\bnonstop\b",
    r"\bmegamix\b",
]

# Reasonable song duration range (in seconds)
MIN_SONG_DURATION = 90  # 1.5 minutes
MAX_SONG_DURATION = 480  # 8 minutes

# TTS voices for announcements (randomly selected each time)
TTS_VOICES = [
    # American English
    "en-US-GuyNeural",
    "en-US-JennyNeural",
    "en-US-AriaNeural",
    "en-US-DavisNeural",
    "en-US-TonyNeural",
    "en-US-SaraNeural",
    "en-US-NancyNeural",
    "en-US-JasonNeural",
    # British English
    "en-GB-RyanNeural",
    "en-GB-SoniaNeural",
    "en-GB-ThomasNeural",
    "en-GB-LibbyNeural",
    # Australian English
    "en-AU-WilliamNeural",
    "en-AU-NatashaNeural",
    # Other English accents
    "en-IN-PrabhatNeural",      # Indian
    "en-IN-NeerjaNeural",
    "en-IE-ConnorNeural",       # Irish
    "en-IE-EmilyNeural",
    "en-ZA-LeahNeural",         # South African
    "en-ZA-LukeNeural",
    "en-NZ-MitchellNeural",     # New Zealand
    "en-NZ-MollyNeural",
    "en-SG-WayneNeural",        # Singaporean
    "en-KE-AsiliaNeural",       # Kenyan
    "en-KE-ChilembaNeural",
    "en-PH-JamesNeural",        # Filipino
    "en-CA-LiamNeural",         # Canadian
    "en-CA-ClaraNeural",
    "en-HK-YanNeural",          # Hong Kong
    # Spanish
    "es-MX-JorgeNeural",        # Mexican
    "es-MX-DaliaNeural",
    "es-ES-AlvaroNeural",       # Spain
    "es-ES-ElviraNeural",
    "es-AR-TomasNeural",        # Argentine
    "es-AR-ElenaNeural",
    "es-CO-GonzaloNeural",      # Colombian
    "es-CO-SalomeNeural",
    "es-CL-CatalinaNeural",     # Chilean
    "es-CL-LorenzoNeural",
    "es-PE-CamilaNeural",       # Peruvian
    "es-PE-AlexNeural",
    "es-VE-PaolaNeural",        # Venezuelan
    "es-VE-SebastianNeural",
    "es-CU-BelkysNeural",       # Cuban
    "es-CU-ManuelNeural",
    "es-PR-KarinaNeural",       # Puerto Rican
    "es-PR-VictorNeural",
]


def is_likely_song(title: str, duration: int = 0) -> bool:
    """Check if a track is likely a song vs other content."""
    title_lower = title.lower()

    # Check for non-song patterns in title
    for pattern in NON_SONG_PATTERNS:
        if re.search(pattern, title_lower, re.IGNORECASE):
            return False

    # Check duration if available
    if duration > 0:
        if duration < MIN_SONG_DURATION or duration > MAX_SONG_DURATION:
            return False

    return True


def build_radio_query(description: str, energy: int = 0) -> str:
    """Build a YouTube search query from description and energy level."""
    modifier = ENERGY_MODIFIERS.get(energy, "")
    query = f"{description} {modifier}".strip()
    return query

logger = logging.getLogger("music-bot.music")

# Patterns to strip from titles for duplicate detection
TITLE_NOISE_PATTERNS = [
    r"\(official\s*(music\s*)?video\)",
    r"\(official\s*audio\)",
    r"\(official\s*lyric\s*video\)",
    r"\(lyric\s*video\)",
    r"\(lyrics?\)",
    r"\(audio\)",
    r"\(visualizer\)",
    r"\(official\s*visualizer\)",
    r"\[official\s*(music\s*)?video\]",
    r"\[official\s*audio\]",
    r"\[lyrics?\]",
    r"\(hd\)",
    r"\(hq\)",
    r"\(4k\)",
    r"\(remaster(ed)?\)",
    r"\(live\)",
    r"\(acoustic\)",
    r"official\s*(music\s*)?video",
    r"\|.*$",  # Remove everything after |
    r"-\s*topic$",  # YouTube auto-generated " - Topic" channels
]


def normalize_title(title: str) -> str:
    """Normalize a title for duplicate comparison by removing common noise."""
    normalized = title.lower().strip()
    for pattern in TITLE_NOISE_PATTERNS:
        normalized = re.sub(pattern, "", normalized, flags=re.IGNORECASE)
    # Collapse multiple spaces and strip
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def format_duration(seconds: int) -> str:
    """Format seconds into mm:ss or hh:mm:ss."""
    if seconds <= 0:
        return "Unknown"
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class NowPlayingView(ui.View):
    """View with playback controls for the now playing message."""

    def __init__(self, cog: "Music", guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    @ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip_button(self, interaction: discord.Interaction, button: ui.Button):
        """Skip the current track."""
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_playing():
            voice_client.stop()
        # Silently acknowledge - the Now Playing message deletion provides feedback
        await interaction.response.defer()

# yt-dlp options
# Use default client fallback (android_vr, ios_downgraded) which works without PO tokens
YDL_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "extract_flat": False,
}

# FFmpeg options for Discord streaming
# loudnorm filter normalizes audio to consistent loudness (EBU R128 standard)
# I=-16: target integrated loudness of -16 LUFS (good for streaming)
# TP=-1.5: true peak limit to prevent clipping
# LRA=11: maintains natural dynamics
# User-Agent header helps avoid 403 errors from YouTube
FFMPEG_OPTS = {
    "before_options": '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -headers "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"',
    "options": "-vn -af loudnorm=I=-16:TP=-1.5:LRA=11",
}

# Autoplay duration in seconds (2 hours)
AUTOPLAY_DURATION = 2 * 60 * 60


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Per-guild music queues and state
        self.queues: dict[int, deque] = {}
        self.current_track: dict[int, dict] = {}
        # Text channel to send now playing messages to
        self.text_channels: dict[int, discord.TextChannel] = {}
        # Autoplay state per guild
        # Structure: {guild_id: {"artist": str, "start_time": datetime, "played_titles": set, "task": asyncio.Task}}
        self.autoplay_state: dict[int, dict] = {}
        # Radio state per guild
        # Structure: {guild_id: {"description": str, "energy": int, "played_titles": set, "avoided_titles": set, "start_time": datetime, "task": asyncio.Task}}
        self.radio_state: dict[int, dict] = {}
        # Track when current song started (for resuming after TTS)
        self.track_start_time: dict[int, datetime] = {}

    def get_queue(self, guild_id: int) -> deque:
        if guild_id not in self.queues:
            self.queues[guild_id] = deque()
        return self.queues[guild_id]

    async def extract_info(self, query: str) -> dict:
        """Extract video info using yt-dlp. Supports URLs or search queries."""
        loop = asyncio.get_event_loop()

        def _extract():
            search_query = query
            if not query.startswith(("http://", "https://")):
                search_query = f"ytsearch:{query}"

            with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
                info = ydl.extract_info(search_query, download=False)

                if "entries" in info:
                    info = info["entries"][0]

                return {
                    "url": info["url"],
                    "title": info.get("title", "Unknown"),
                    "duration": info.get("duration", 0),
                    "webpage_url": info.get("webpage_url", query),
                }

        return await loop.run_in_executor(None, _extract)

    async def search_artist_songs(self, artist: str, max_results: int = 20) -> list[dict]:
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

    async def get_full_track_info(self, video_id: str) -> dict:
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

    def play_next(self, guild_id: int, voice_client: discord.VoiceClient):
        """Play the next track in the queue (without TTS announcement)."""
        queue = self.get_queue(guild_id)

        if not queue:
            self.current_track.pop(guild_id, None)
            cleanup.clear_now_playing(guild_id)
            return

        track = queue.popleft()
        self.current_track[guild_id] = track
        self._start_track(guild_id, voice_client, track)

    async def play_next_async(self, guild_id: int, voice_client: discord.VoiceClient):
        """Async wrapper for play_next that announces and plays the next track."""
        if not voice_client or not voice_client.is_connected():
            return

        queue = self.get_queue(guild_id)
        if not queue:
            self.current_track.pop(guild_id, None)
            cleanup.clear_now_playing(guild_id)
            return

        track = queue.popleft()
        self.current_track[guild_id] = track

        # Announce the track with TTS
        await self._announce_track(guild_id, voice_client, track)

    async def _announce_track(self, guild_id: int, voice_client: discord.VoiceClient, track: dict):
        """Announce track title with TTS then start playing."""
        title = track.get("title", "Unknown")
        # Shorten long titles for TTS
        if len(title) > 60:
            title = title[:60]

        try:
            voice = random.choice(TTS_VOICES)
            communicate = edge_tts.Communicate(f"Now playing: {title}", voice)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = tmp.name
            await communicate.save(tmp_path)
        except Exception as e:
            logger.error(f"TTS announcement failed: {e}")
            # Fall back to just playing without announcement
            self._start_track(guild_id, voice_client, track)
            await self.send_now_playing(guild_id)
            return

        def after_announce(error):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            if error:
                logger.error(f"Announcement error: {error}")
            # Start the actual track after announcement
            asyncio.run_coroutine_threadsafe(
                self._start_track_async(guild_id, voice_client, track),
                self.bot.loop,
            )

        voice_client.play(discord.FFmpegPCMAudio(tmp_path), after=after_announce)

    def _start_track(self, guild_id: int, voice_client: discord.VoiceClient, track: dict):
        """Start playing a track."""
        self.track_start_time[guild_id] = datetime.now()
        source = discord.FFmpegPCMAudio(track["url"], **FFMPEG_OPTS)

        def after_playing(error):
            if error:
                logger.error(f"Player error: {error}")
            asyncio.run_coroutine_threadsafe(
                self.play_next_async(guild_id, voice_client),
                self.bot.loop,
            )

        voice_client.play(source, after=after_playing)

    async def _start_track_async(self, guild_id: int, voice_client: discord.VoiceClient, track: dict):
        """Async wrapper to start track and send now playing message."""
        if voice_client and voice_client.is_connected():
            self._start_track(guild_id, voice_client, track)
            await self.send_now_playing(guild_id)

    async def send_now_playing(self, guild_id: int):
        """Send a now playing embed with skip button."""
        track = self.current_track.get(guild_id)
        channel = self.text_channels.get(guild_id)

        if not track or not channel:
            return

        queue = self.get_queue(guild_id)

        embed = discord.Embed(
            title="Now Playing",
            description=f"**{track['title']}**",
            color=discord.Color.green(),
        )
        embed.add_field(name="Duration", value=format_duration(track.get("duration", 0)), inline=True)
        embed.add_field(name="Up Next", value=f"{len(queue)} track(s)", inline=True)

        if track.get("webpage_url"):
            embed.add_field(name="Link", value=f"[YouTube]({track['webpage_url']})", inline=True)

        view = NowPlayingView(self, guild_id)

        try:
            msg = await channel.send(embed=embed, view=view)
            # Track this message for deletion when the next track starts
            cleanup.track_now_playing(guild_id, msg)
        except Exception as e:
            logger.error(f"Failed to send now playing message: {e}")

    async def autoplay_loop(self, guild_id: int, voice_client: discord.VoiceClient, channel: discord.TextChannel):
        """Background task that keeps adding songs to the queue during autoplay."""
        state = self.autoplay_state.get(guild_id)
        if not state:
            return

        artist = state["artist"]
        logger.info(f"Autoplay started for artist: {artist} in guild {guild_id}")

        try:
            while True:
                elapsed = (datetime.now() - state["start_time"]).total_seconds()
                if elapsed >= AUTOPLAY_DURATION:
                    await cleanup.send_to_channel_temp(
                        channel,
                        f"Autoplay ended after 2 hours. Played {len(state['played_titles'])} unique songs.",
                        delay=MessageCleanup.AUTOPLAY_END
                    )
                    break

                if not voice_client or not voice_client.is_connected():
                    logger.info("Voice client disconnected, stopping autoplay")
                    break

                queue = self.get_queue(guild_id)
                if len(queue) < 3:
                    logger.debug(f"Queue low ({len(queue)} tracks), fetching more songs for {artist}")

                    try:
                        songs = await self.search_artist_songs(artist, max_results=30)
                    except Exception as e:
                        logger.error(f"Error searching for songs: {e}")
                        await asyncio.sleep(10)
                        continue

                    new_songs = [s for s in songs if normalize_title(s["title"]) not in state["played_titles"]]

                    if not new_songs:
                        logger.info(f"All known songs played for {artist}, trying different search")
                        try:
                            songs = await self.search_artist_songs(f"{artist} songs", max_results=30)
                            new_songs = [s for s in songs if normalize_title(s["title"]) not in state["played_titles"]]
                        except Exception as e:
                            logger.error(f"Error in fallback search: {e}")

                    if not new_songs:
                        await cleanup.send_to_channel_temp(
                            channel,
                            f"Ran out of new songs for **{artist}**. Stopping autoplay.",
                            delay=MessageCleanup.AUTOPLAY_END
                        )
                        break

                    random.shuffle(new_songs)
                    songs_to_add = new_songs[:5]

                    for song in songs_to_add:
                        try:
                            track = await self.get_full_track_info(song["id"])
                            state["played_titles"].add(normalize_title(track["title"]))
                            queue.append(track)
                            logger.debug(f"Added to queue: {track['title']}")
                        except Exception as e:
                            logger.error(f"Error getting track info for {song['title']}: {e}")
                            continue

                    if not voice_client.is_playing() and not voice_client.is_paused() and queue:
                        await self.play_next_async(guild_id, voice_client)

                await asyncio.sleep(5)

        except asyncio.CancelledError:
            logger.info(f"Autoplay task cancelled for guild {guild_id}")
        except Exception as e:
            logger.error(f"Autoplay error: {e}")
            await cleanup.send_to_channel_temp(
                channel,
                f"Autoplay error: {e}",
                delay=MessageCleanup.ERROR
            )
        finally:
            self.autoplay_state.pop(guild_id, None)
            logger.info(f"Autoplay ended for guild {guild_id}")

    async def search_radio_songs(self, query: str, max_results: int = 30) -> list[dict]:
        """Search YouTube for songs matching a radio query."""
        loop = asyncio.get_event_loop()

        def _search():
            search_query = f"ytsearch{max_results}:{query} music"
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

    async def get_related_videos(self, video_id: str, max_results: int = 20) -> list[dict]:
        """Get YouTube's recommended/related videos for a given video."""
        loop = asyncio.get_event_loop()

        def _get_related():
            url = f"https://www.youtube.com/watch?v={video_id}"
            opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
            }

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                related = []

                # yt-dlp provides related videos in 'related_videos' or similar fields
                # Try multiple possible field names
                related_entries = (
                    info.get("related_videos", []) or
                    info.get("entries", []) or
                    []
                )

                for entry in related_entries[:max_results]:
                    if entry and entry.get("id"):
                        related.append({
                            "id": entry.get("id"),
                            "title": entry.get("title", "Unknown"),
                            "webpage_url": f"https://www.youtube.com/watch?v={entry.get('id')}",
                        })

                return related

        return await loop.run_in_executor(None, _get_related)

    async def radio_loop(self, guild_id: int, voice_client: discord.VoiceClient, channel: discord.TextChannel):
        """Background task that keeps the radio playing using YouTube's recommendation engine."""
        state = self.radio_state.get(guild_id)
        if not state:
            return

        logger.info(f"Radio started with description: {state['description']} in guild {guild_id}")

        # Track the last video ID we got recommendations from
        last_seed_id = None

        try:
            while True:
                # Check if radio state still exists (might be stopped)
                state = self.radio_state.get(guild_id)
                if not state:
                    break

                elapsed = (datetime.now() - state["start_time"]).total_seconds()
                if elapsed >= AUTOPLAY_DURATION:
                    await cleanup.send_to_channel_temp(
                        channel,
                        f"📻 Radio ended after 2 hours. Played {len(state['played_titles'])} tracks.",
                        delay=MessageCleanup.AUTOPLAY_END
                    )
                    break

                if not voice_client or not voice_client.is_connected():
                    logger.info("Voice client disconnected, stopping radio")
                    break

                queue = self.get_queue(guild_id)
                if len(queue) < 3:
                    played = state["played_titles"]
                    avoided = state["avoided_titles"]
                    new_songs = []

                    # Strategy: Use YouTube recommendations if we have a current track
                    # Otherwise, search based on description
                    current = self.current_track.get(guild_id)
                    seed_id = state.get("seed_id")

                    # Try to get recommendations from current track or last seed
                    if current and current.get("webpage_url"):
                        # Extract video ID from current track
                        current_id = current["webpage_url"].split("v=")[-1].split("&")[0]
                        if current_id and current_id != last_seed_id:
                            logger.debug(f"Getting recommendations from current track: {current['title']}")
                            try:
                                related = await self.get_related_videos(current_id, max_results=20)
                                new_songs = [
                                    s for s in related
                                    if normalize_title(s["title"]) not in played
                                    and normalize_title(s["title"]) not in avoided
                                ]
                                if new_songs:
                                    last_seed_id = current_id
                                    logger.debug(f"Got {len(new_songs)} recommendations from YouTube")
                            except Exception as e:
                                logger.error(f"Error getting recommendations: {e}")

                    # If no recommendations, search based on description
                    if not new_songs:
                        description = state["description"]
                        energy = state["energy"]
                        query = build_radio_query(description, energy)
                        logger.debug(f"Searching for: {query}")

                        try:
                            songs = await self.search_radio_songs(query, max_results=30)
                            new_songs = [
                                s for s in songs
                                if normalize_title(s["title"]) not in played
                                and normalize_title(s["title"]) not in avoided
                            ]
                        except Exception as e:
                            logger.error(f"Error searching for songs: {e}")
                            await asyncio.sleep(10)
                            continue

                    # If still nothing, try a variation
                    if not new_songs:
                        logger.info("No new songs found, trying variation")
                        try:
                            description = state["description"]
                            songs = await self.search_radio_songs(f"{description} songs playlist mix", max_results=30)
                            new_songs = [
                                s for s in songs
                                if normalize_title(s["title"]) not in played
                                and normalize_title(s["title"]) not in avoided
                            ]
                        except Exception as e:
                            logger.error(f"Error in fallback search: {e}")

                    if not new_songs:
                        await cleanup.send_to_channel_temp(
                            channel,
                            f"📻 Running low on new tracks. Try `!tune` to explore a new direction.",
                            delay=MessageCleanup.AUTOPLAY_EVENT
                        )
                        await asyncio.sleep(30)
                        continue

                    random.shuffle(new_songs)
                    songs_to_add = new_songs[:8]  # Try more to account for filtered ones
                    added_count = 0

                    for song in songs_to_add:
                        if added_count >= 5:
                            break
                        try:
                            track = await self.get_full_track_info(song["id"])

                            # Filter out non-songs (interviews, podcasts, compilations, etc.)
                            if not is_likely_song(track["title"], track.get("duration", 0)):
                                logger.debug(f"Filtered out non-song: {track['title']}")
                                state["played_titles"].add(normalize_title(track["title"]))  # Don't try again
                                continue

                            state["played_titles"].add(normalize_title(track["title"]))
                            queue.append(track)
                            added_count += 1
                            logger.debug(f"Added to queue: {track['title']}")

                            # Store the first track as seed for future recommendations
                            if not state.get("seed_id"):
                                state["seed_id"] = song["id"]
                        except Exception as e:
                            logger.error(f"Error getting track info for {song['title']}: {e}")
                            continue

                    if not voice_client.is_playing() and not voice_client.is_paused() and queue:
                        await self.play_next_async(guild_id, voice_client)

                await asyncio.sleep(5)

        except asyncio.CancelledError:
            logger.info(f"Radio task cancelled for guild {guild_id}")
        except Exception as e:
            logger.error(f"Radio error: {e}")
            await cleanup.send_to_channel_temp(
                channel,
                f"📻 Radio error: {e}",
                delay=MessageCleanup.ERROR
            )
        finally:
            self.radio_state.pop(guild_id, None)
            logger.info(f"Radio ended for guild {guild_id}")

    def stop_radio_and_autoplay(self, guild_id: int):
        """Stop any running radio or autoplay for a guild."""
        # Stop autoplay if running
        if guild_id in self.autoplay_state:
            task = self.autoplay_state[guild_id].get("task")
            if task:
                task.cancel()
            self.autoplay_state.pop(guild_id, None)

        # Stop radio if running
        if guild_id in self.radio_state:
            task = self.radio_state[guild_id].get("task")
            if task:
                task.cancel()
            self.radio_state.pop(guild_id, None)

    @commands.command(name="join")
    async def join(self, ctx: commands.Context):
        """Join the user's voice channel."""
        if not ctx.author.voice:
            await cleanup.send_error(ctx, "You're not in a voice channel!")
            return

        channel = ctx.author.voice.channel

        if ctx.voice_client:
            await ctx.voice_client.move_to(channel)
        else:
            await channel.connect()

        await cleanup.send_ack(ctx, f"Joined **{channel.name}**")

    @commands.command(name="leave")
    async def leave(self, ctx: commands.Context):
        """Leave the voice channel."""
        if not ctx.voice_client:
            await cleanup.send_error(ctx, "I'm not in a voice channel!")
            return

        guild_id = ctx.guild.id
        self.queues.pop(guild_id, None)
        self.current_track.pop(guild_id, None)
        cleanup.clear_now_playing(guild_id)

        await ctx.voice_client.disconnect()
        await cleanup.send_ack(ctx, "Left the voice channel")

    @commands.command(name="play", aliases=["p"])
    async def play(self, ctx: commands.Context, *, url: str):
        """Play a YouTube URL or add it to the queue."""
        if not ctx.voice_client:
            if not ctx.author.voice:
                await cleanup.send_error(ctx, "You're not in a voice channel!")
                return
            await ctx.author.voice.channel.connect()

        for _ in range(20):
            if ctx.voice_client.is_connected():
                break
            await asyncio.sleep(0.5)
        else:
            await cleanup.send_error(ctx, "Failed to connect to voice channel. Try again.")
            return

        # Send temporary "fetching" message
        fetch_msg = await ctx.send(f"Fetching: `{url}`...")
        cleanup.schedule_delete(fetch_msg, MessageCleanup.TEMPORARY)

        try:
            track = await self.extract_info(url)
        except Exception as e:
            await cleanup.send_error(ctx, f"Error fetching video: {e}")
            return

        guild_id = ctx.guild.id
        queue = self.get_queue(guild_id)
        self.text_channels[guild_id] = ctx.channel

        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            queue.append(track)
            await cleanup.send_temp(ctx, f"Added to queue: **{track['title']}** (position {len(queue)})", delay=MessageCleanup.QUEUE_ADD)
        else:
            queue.append(track)
            await self.play_next_async(guild_id, ctx.voice_client)

    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context):
        """Pause playback."""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await cleanup.send_ack(ctx, "Paused")
        else:
            await cleanup.send_error(ctx, "Nothing is playing")

    @commands.command(name="resume")
    async def resume(self, ctx: commands.Context):
        """Resume playback."""
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await cleanup.send_ack(ctx, "Resumed")
        else:
            await cleanup.send_error(ctx, "Nothing is paused")

    @commands.command(name="skip", aliases=["s"])
    async def skip(self, ctx: commands.Context):
        """Skip the current track."""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            await cleanup.send_ack(ctx, "Skipped")
        else:
            await cleanup.send_error(ctx, "Nothing is playing")

    @commands.command(name="stop")
    async def stop(self, ctx: commands.Context):
        """Stop playback and clear the queue."""
        guild_id = ctx.guild.id

        self.queues.pop(guild_id, None)
        self.current_track.pop(guild_id, None)
        cleanup.clear_now_playing(guild_id)

        if ctx.voice_client:
            ctx.voice_client.stop()

        await cleanup.send_ack(ctx, "Stopped and cleared queue")

    @commands.command(name="queue", aliases=["q"])
    async def show_queue(self, ctx: commands.Context):
        """Show the current queue."""
        guild_id = ctx.guild.id
        queue = self.get_queue(guild_id)
        now = self.current_track.get(guild_id)

        if not now and not queue:
            await cleanup.send_error(ctx, "Queue is empty")
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

        await cleanup.send_status(ctx, "\n".join(lines))

    @commands.command(name="np", aliases=["nowplaying"])
    async def now_playing(self, ctx: commands.Context):
        """Show the currently playing track."""
        guild_id = ctx.guild.id
        now = self.current_track.get(guild_id)

        if now:
            await cleanup.send_status(ctx, f"Now playing: **{now['title']}**")
        else:
            await cleanup.send_error(ctx, "Nothing is playing")

    @commands.command(name="clear")
    async def clear_queue(self, ctx: commands.Context):
        """Clear the queue and stop the current track."""
        guild_id = ctx.guild.id
        queue = self.get_queue(guild_id)
        queue.clear()
        self.current_track.pop(guild_id, None)
        cleanup.clear_now_playing(guild_id)
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        await cleanup.send_ack(ctx, "Queue cleared and playback stopped")

    @commands.command(name="autoplay", aliases=["ap"])
    async def autoplay(self, ctx: commands.Context, *, artist: str):
        """Start autoplay mode for an artist. Plays songs for 2 hours with no duplicates.
        Usage: !autoplay <artist name>

        If autoplay is already running, this will stop the current song,
        clear the queue, and start fresh with the new artist.
        """
        guild_id = ctx.guild.id

        # Stop existing autoplay if running
        if guild_id in self.autoplay_state:
            old_task = self.autoplay_state[guild_id].get("task")
            if old_task:
                old_task.cancel()
            self.autoplay_state.pop(guild_id, None)

        # Clear queue and stop current song for fresh start
        self.queues.pop(guild_id, None)
        self.current_track.pop(guild_id, None)
        cleanup.clear_now_playing(guild_id)
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()

        if not ctx.voice_client:
            if not ctx.author.voice:
                await cleanup.send_error(ctx, "You're not in a voice channel!")
                return
            await ctx.author.voice.channel.connect()

        for _ in range(20):
            if ctx.voice_client.is_connected():
                break
            await asyncio.sleep(0.5)
        else:
            await cleanup.send_error(ctx, "Failed to connect to voice channel. Try again.")
            return

        await cleanup.send_temp(ctx, f"Starting autoplay for **{artist}** (2 hours, no duplicates)...", delay=MessageCleanup.AUTOPLAY_EVENT)

        self.text_channels[guild_id] = ctx.channel
        self.autoplay_state[guild_id] = {
            "artist": artist,
            "start_time": datetime.now(),
            "played_titles": set(),
            "task": None,
        }

        task = asyncio.create_task(self.autoplay_loop(guild_id, ctx.voice_client, ctx.channel))
        self.autoplay_state[guild_id]["task"] = task

    @commands.command(name="stopautoplay", aliases=["sap"])
    async def stop_autoplay(self, ctx: commands.Context):
        """Stop autoplay mode."""
        guild_id = ctx.guild.id

        if guild_id not in self.autoplay_state:
            await cleanup.send_error(ctx, "Autoplay is not running.")
            return

        state = self.autoplay_state[guild_id]
        task = state.get("task")
        if task:
            task.cancel()

        played_count = len(state.get("played_titles", set()))
        self.autoplay_state.pop(guild_id, None)

        await cleanup.send_temp(ctx, f"Autoplay stopped. Played {played_count} unique songs.", delay=MessageCleanup.AUTOPLAY_EVENT)

    @commands.command(name="autoplaystatus", aliases=["aps"])
    async def autoplay_status(self, ctx: commands.Context):
        """Check autoplay status."""
        guild_id = ctx.guild.id

        if guild_id not in self.autoplay_state:
            await cleanup.send_error(ctx, "Autoplay is not running.")
            return

        state = self.autoplay_state[guild_id]
        artist = state["artist"]
        elapsed = (datetime.now() - state["start_time"]).total_seconds()
        remaining = max(0, AUTOPLAY_DURATION - elapsed)
        played_count = len(state["played_titles"])

        mins_remaining = int(remaining // 60)
        await cleanup.send_status(
            ctx,
            f"Autoplay: **{artist}**\n"
            f"Songs played: {played_count}\n"
            f"Time remaining: {mins_remaining} minutes"
        )

    # ==================== RADIO COMMANDS ====================

    @commands.command(name="radio", aliases=["r"])
    async def radio(self, ctx: commands.Context, *, description: str):
        """Start radio mode with a description of the sound you want.
        Usage: !radio <description>

        Examples:
            !radio late night jazz cafe
            !radio 90s hip hop bangers
            !radio chill lo-fi beats for studying
        """
        guild_id = ctx.guild.id

        # Stop any existing radio or autoplay
        self.stop_radio_and_autoplay(guild_id)

        # Clear queue and stop current song for fresh start
        self.queues.pop(guild_id, None)
        self.current_track.pop(guild_id, None)
        cleanup.clear_now_playing(guild_id)
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()

        if not ctx.voice_client:
            if not ctx.author.voice:
                await cleanup.send_error(ctx, "You're not in a voice channel!")
                return
            await ctx.author.voice.channel.connect()

        for _ in range(20):
            if ctx.voice_client.is_connected():
                break
            await asyncio.sleep(0.5)
        else:
            await cleanup.send_error(ctx, "Failed to connect to voice channel. Try again.")
            return

        await cleanup.send_temp(
            ctx,
            f"📻 Tuning radio to **{description}**...",
            delay=MessageCleanup.AUTOPLAY_EVENT
        )

        self.text_channels[guild_id] = ctx.channel
        self.radio_state[guild_id] = {
            "description": description,
            "energy": 0,
            "played_titles": set(),
            "avoided_titles": set(),
            "start_time": datetime.now(),
            "task": None,
        }

        task = asyncio.create_task(self.radio_loop(guild_id, ctx.voice_client, ctx.channel))
        self.radio_state[guild_id]["task"] = task

    @commands.command(name="signal")
    async def signal(self, ctx: commands.Context):
        """Show what the radio is currently tuned to."""
        guild_id = ctx.guild.id

        if guild_id not in self.radio_state:
            await cleanup.send_error(ctx, "📻 Radio is not running. Start with `!radio <description>`")
            return

        state = self.radio_state[guild_id]
        description = state["description"]
        energy = state["energy"]
        elapsed = (datetime.now() - state["start_time"]).total_seconds()
        remaining = max(0, AUTOPLAY_DURATION - elapsed)
        played_count = len(state["played_titles"])
        avoided_count = len(state["avoided_titles"])

        energy_display = {-2: "⬇️⬇️", -1: "⬇️", 0: "➡️", 1: "⬆️", 2: "⬆️⬆️"}
        mins_remaining = int(remaining // 60)

        await cleanup.send_status(
            ctx,
            f"📻 **Radio Signal**\n"
            f"Tuned to: **{description}**\n"
            f"Energy: {energy_display.get(energy, '➡️')} ({energy:+d})\n"
            f"Tracks played: {played_count}\n"
            f"Tracks avoided: {avoided_count}\n"
            f"Time remaining: {mins_remaining} minutes"
        )

    @commands.command(name="tune")
    async def tune(self, ctx: commands.Context, *, new_description: str):
        """Change the radio's direction mid-session.
        Usage: !tune <new description>

        This clears the queue and shifts to the new sound.
        """
        guild_id = ctx.guild.id

        if guild_id not in self.radio_state:
            await cleanup.send_error(ctx, "📻 Radio is not running. Start with `!radio <description>`")
            return

        state = self.radio_state[guild_id]
        old_description = state["description"]
        state["description"] = new_description

        # Clear the queue to immediately shift direction
        queue = self.get_queue(guild_id)
        queue.clear()

        await cleanup.send_temp(
            ctx,
            f"📻 Retuning from **{old_description}** → **{new_description}**",
            delay=MessageCleanup.AUTOPLAY_EVENT
        )

    @commands.command(name="dial")
    async def dial(self, ctx: commands.Context, direction: str):
        """Adjust the radio's energy level.
        Usage: !dial up  or  !dial down

        Energy affects the vibe:
            down down: slow, ambient, calm
            down: chill, mellow
            (neutral): as described
            up: upbeat, energetic
            up up: hype, intense
        """
        guild_id = ctx.guild.id

        if guild_id not in self.radio_state:
            await cleanup.send_error(ctx, "📻 Radio is not running. Start with `!radio <description>`")
            return

        state = self.radio_state[guild_id]
        direction = direction.lower()

        if direction == "up":
            state["energy"] = min(2, state["energy"] + 1)
        elif direction == "down":
            state["energy"] = max(-2, state["energy"] - 1)
        else:
            await cleanup.send_error(ctx, "Usage: `!dial up` or `!dial down`")
            return

        # Clear queue to apply new energy
        queue = self.get_queue(guild_id)
        queue.clear()

        energy = state["energy"]
        energy_display = {-2: "⬇️⬇️ very chill", -1: "⬇️ chill", 0: "➡️ neutral", 1: "⬆️ energetic", 2: "⬆️⬆️ hype"}

        await cleanup.send_temp(
            ctx,
            f"📻 Dial adjusted: {energy_display.get(energy, 'neutral')}",
            delay=MessageCleanup.AUTOPLAY_EVENT
        )

    @commands.command(name="static")
    async def static(self, ctx: commands.Context):
        """Skip the current track and avoid similar ones.
        Use this when a track doesn't fit the vibe.
        """
        guild_id = ctx.guild.id

        if guild_id not in self.radio_state:
            await cleanup.send_error(ctx, "📻 Radio is not running. Start with `!radio <description>`")
            return

        current = self.current_track.get(guild_id)
        if not current:
            await cleanup.send_error(ctx, "Nothing is playing")
            return

        # Add to avoided titles
        state = self.radio_state[guild_id]
        state["avoided_titles"].add(normalize_title(current["title"]))

        # Skip the track
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()

        await cleanup.send_temp(
            ctx,
            f"📻 Static! Avoiding **{current['title'][:50]}**...",
            delay=MessageCleanup.ACK
        )

    @commands.command(name="stopradio", aliases=["sr"])
    async def stop_radio(self, ctx: commands.Context):
        """Stop radio mode."""
        guild_id = ctx.guild.id

        if guild_id not in self.radio_state:
            await cleanup.send_error(ctx, "📻 Radio is not running.")
            return

        state = self.radio_state[guild_id]
        task = state.get("task")
        if task:
            task.cancel()

        played_count = len(state.get("played_titles", set()))
        self.radio_state.pop(guild_id, None)

        await cleanup.send_temp(
            ctx,
            f"📻 Radio off. Played {played_count} tracks.",
            delay=MessageCleanup.AUTOPLAY_EVENT
        )

    # ==================== STATION COMMANDS ====================

    @commands.command(name="station")
    async def station(self, ctx: commands.Context, action: str = None, *, name: str = None):
        """Save or load radio station presets.
        Usage:
            !station save <name>  - Save current radio as a station
            !station <name>       - Load and play a saved station
            !station delete <name> - Delete a saved station
        """
        guild_id = ctx.guild.id
        guild_key = str(guild_id)

        if action is None:
            await cleanup.send_error(ctx, "Usage: `!station save <name>`, `!station <name>`, or `!station delete <name>`")
            return

        stations = load_stations()
        if guild_key not in stations:
            stations[guild_key] = {}

        # Handle "save" action
        if action.lower() == "save":
            if not name:
                await cleanup.send_error(ctx, "Please provide a station name: `!station save <name>`")
                return

            if guild_id not in self.radio_state:
                await cleanup.send_error(ctx, "📻 Radio is not running. Start with `!radio <description>` first.")
                return

            state = self.radio_state[guild_id]
            stations[guild_key][name.lower()] = {
                "description": state["description"],
                "energy": state["energy"],
            }
            save_stations(stations)

            await cleanup.send_ack(ctx, f"📻 Station **{name}** saved!")
            return

        # Handle "delete" action
        if action.lower() == "delete":
            if not name:
                await cleanup.send_error(ctx, "Please provide a station name: `!station delete <name>`")
                return

            if name.lower() not in stations[guild_key]:
                await cleanup.send_error(ctx, f"Station **{name}** not found.")
                return

            del stations[guild_key][name.lower()]
            save_stations(stations)

            await cleanup.send_ack(ctx, f"📻 Station **{name}** deleted.")
            return

        # Otherwise, treat action as station name to load
        station_name = action.lower() if not name else f"{action} {name}".lower()

        if station_name not in stations[guild_key]:
            await cleanup.send_error(ctx, f"Station **{station_name}** not found. Use `!stations` to see saved stations.")
            return

        station_data = stations[guild_key][station_name]

        # Stop any existing radio or autoplay
        self.stop_radio_and_autoplay(guild_id)

        # Clear queue and stop current song
        self.queues.pop(guild_id, None)
        self.current_track.pop(guild_id, None)
        cleanup.clear_now_playing(guild_id)
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()

        if not ctx.voice_client:
            if not ctx.author.voice:
                await cleanup.send_error(ctx, "You're not in a voice channel!")
                return
            await ctx.author.voice.channel.connect()

        for _ in range(20):
            if ctx.voice_client.is_connected():
                break
            await asyncio.sleep(0.5)
        else:
            await cleanup.send_error(ctx, "Failed to connect to voice channel. Try again.")
            return

        await cleanup.send_temp(
            ctx,
            f"📻 Loading station **{station_name}**: {station_data['description']}",
            delay=MessageCleanup.AUTOPLAY_EVENT
        )

        self.text_channels[guild_id] = ctx.channel
        self.radio_state[guild_id] = {
            "description": station_data["description"],
            "energy": station_data.get("energy", 0),
            "played_titles": set(),
            "avoided_titles": set(),
            "start_time": datetime.now(),
            "task": None,
        }

        task = asyncio.create_task(self.radio_loop(guild_id, ctx.voice_client, ctx.channel))
        self.radio_state[guild_id]["task"] = task

    @commands.command(name="stations")
    async def list_stations(self, ctx: commands.Context):
        """List all saved radio stations for this server."""
        guild_id = ctx.guild.id
        guild_key = str(guild_id)

        stations = load_stations()
        guild_stations = stations.get(guild_key, {})

        if not guild_stations:
            await cleanup.send_error(ctx, "No saved stations. Use `!station save <name>` while radio is playing.")
            return

        lines = ["📻 **Saved Stations:**"]
        for name, data in guild_stations.items():
            energy = data.get("energy", 0)
            energy_icon = {-2: "⬇️⬇️", -1: "⬇️", 0: "", 1: "⬆️", 2: "⬆️⬆️"}.get(energy, "")
            lines.append(f"• **{name}** - {data['description']} {energy_icon}")

        await cleanup.send_status(ctx, "\n".join(lines))

    # ==================== EASTER EGG ====================

    @commands.command(name="wisper")
    async def wisper(self, ctx: commands.Context, *, message: str):
        """Whisper a secret message using text-to-speech."""
        try:
            await ctx.message.delete()
        except Exception:
            pass

        if not ctx.voice_client:
            if not ctx.author.voice:
                return
            await ctx.author.voice.channel.connect()

        guild_id = ctx.guild.id
        voice_client = ctx.voice_client

        # Store current state for resuming
        was_playing = voice_client.is_playing()
        current = self.current_track.get(guild_id)
        elapsed = 0
        if was_playing and current and guild_id in self.track_start_time:
            elapsed = (datetime.now() - self.track_start_time[guild_id]).total_seconds()

        # Generate TTS
        try:
            voice = random.choice(TTS_VOICES)
            communicate = edge_tts.Communicate(message, voice)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = tmp.name
            await communicate.save(tmp_path)
        except Exception as e:
            logger.error(f"TTS failed: {e}")
            return

        # Stop music to play TTS
        if was_playing:
            voice_client.stop()

        def after_tts(error):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            if was_playing and current:
                asyncio.run_coroutine_threadsafe(
                    self._resume_track(guild_id, voice_client, current, elapsed),
                    self.bot.loop,
                )

        voice_client.play(discord.FFmpegPCMAudio(tmp_path), after=after_tts)

    async def _resume_track(self, guild_id: int, voice_client: discord.VoiceClient, track: dict, seek_seconds: float):
        """Resume a track from a specific position."""
        if not voice_client or not voice_client.is_connected():
            return

        self.current_track[guild_id] = track
        self.track_start_time[guild_id] = datetime.now() - timedelta(seconds=seek_seconds)

        # Use ffmpeg seek to resume from position
        ffmpeg_opts = {
            "before_options": f"-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -ss {int(seek_seconds)}",
            "options": "-vn -af loudnorm=I=-16:TP=-1.5:LRA=11",
        }
        source = discord.FFmpegPCMAudio(track["url"], **ffmpeg_opts)

        def after_playing(error):
            if error:
                logger.error(f"Player error: {error}")
            asyncio.run_coroutine_threadsafe(
                self.play_next_async(guild_id, voice_client),
                self.bot.loop,
            )

        voice_client.play(source, after=after_playing)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
