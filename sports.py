import logging
from datetime import datetime, timedelta

import aiohttp
from discord.ext import commands

from bot import cleanup, MessageCleanup

logger = logging.getLogger("music-bot.sports")

# ESPN API endpoints
ESPN_ENDPOINTS = {
    "NFL": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "NBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "NHL": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard",
}

# Team emojis
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

                    away = competitors[0] if competitors[0].get("homeAway") == "away" else competitors[1]
                    home = competitors[1] if competitors[1].get("homeAway") == "home" else competitors[0]

                    status = event.get("status", {})
                    state = status.get("type", {}).get("state", "")
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
    """Format a live game."""
    away = game["away_team"]
    home = game["home_team"]
    league = game["league"]
    away_score = int(game["away_score"])
    home_score = int(game["home_score"])
    detail = game["detail"]

    away_emoji = get_team_emoji(away, league)
    home_emoji = get_team_emoji(home, league)

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
    """Format a finished game."""
    away = game["away_team"]
    home = game["home_team"]
    league = game["league"]
    away_score = int(game["away_score"])
    home_score = int(game["home_score"])

    away_emoji = get_team_emoji(away, league)
    home_emoji = get_team_emoji(home, league)

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
    """Format a scheduled game."""
    away = game["away_team"]
    home = game["home_team"]
    league = game["league"]
    detail = game["detail"]

    away_emoji = get_team_emoji(away, league)
    home_emoji = get_team_emoji(home, league)

    return f"⏰ `{detail}` │ {away_emoji} {away}  @  {home_emoji} {home}"


class Sports(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="cs", aliases=["scores"])
    async def current_scores(self, ctx: commands.Context, league: str = None):
        """Show current sports scores. Usage: !cs [nfl|nba|nhl] or !cs for all."""
        leagues = ["NFL", "NBA", "NHL"]

        if league:
            league = league.upper()
            if league not in leagues:
                await cleanup.send_error(ctx, f"Unknown league. Use: {', '.join(leagues)}")
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

            live = [g for g in games if g["state"] == "in"]
            finished = [g for g in games if g["state"] == "post"]
            scheduled = [g for g in games if g["state"] == "pre"]

            output.append(f"{icon} **━━━ {lg} SCOREBOARD ━━━** {icon}")

            if live:
                output.append("🔥 **LIVE**")
                output.extend(format_game_live(g) for g in live)

            if finished:
                output.append("📊 **FINAL**")
                output.extend(format_game_final(g) for g in finished)

            if scheduled:
                output.append("📅 **UPCOMING**")
                output.extend(format_game_scheduled(g) for g in scheduled)

            output.append("")

        await cleanup.send_temp(ctx, "\n".join(output).strip(), delay=MessageCleanup.SCORES)


async def setup(bot: commands.Bot):
    await bot.add_cog(Sports(bot))
