import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# Set up verbose logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)-20s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("music-bot")


class MessageCleanup:
    """Manages automatic deletion of bot messages to reduce chat clutter."""

    # Deletion delay constants (in seconds)
    ERROR = 15
    ACK = 30
    TEMPORARY = 10
    QUEUE_ADD = 45
    STATUS_DISPLAY = 120  # 2 minutes
    AUTOPLAY_EVENT = 60
    AUTOPLAY_END = 300  # 5 minutes
    SCORES = 300  # 5 minutes
    USER_COMMAND = 20  # User command messages

    def __init__(self):
        # Track "now playing" messages per guild for deletion on track change
        self.now_playing_messages: dict[int, discord.Message] = {}

    async def delete_after(self, message: discord.Message, delay: float):
        """Schedule a message for deletion after a delay."""
        try:
            await asyncio.sleep(delay)
            await message.delete()
        except discord.NotFound:
            pass  # Message already deleted
        except discord.Forbidden:
            logger.warning(f"Missing permissions to delete message {message.id}")
        except Exception as e:
            logger.error(f"Error deleting message: {e}")

    def schedule_delete(self, message: discord.Message, delay: float):
        """Schedule a message for deletion (non-blocking)."""
        asyncio.create_task(self.delete_after(message, delay))

    async def send_temp(
        self,
        ctx: commands.Context,
        content: str = None,
        delay: float = ACK,
        **kwargs
    ) -> discord.Message:
        """Send a message and schedule it for deletion."""
        msg = await ctx.send(content, **kwargs)
        self.schedule_delete(msg, delay)
        return msg

    async def send_error(self, ctx: commands.Context, content: str, **kwargs) -> discord.Message:
        """Send an error message (deleted after 15 seconds)."""
        return await self.send_temp(ctx, content, delay=self.ERROR, **kwargs)

    async def send_ack(self, ctx: commands.Context, content: str, **kwargs) -> discord.Message:
        """Send an acknowledgment message (deleted after 30 seconds)."""
        return await self.send_temp(ctx, content, delay=self.ACK, **kwargs)

    async def send_status(self, ctx: commands.Context, content: str = None, **kwargs) -> discord.Message:
        """Send a status display message (deleted after 2 minutes)."""
        return await self.send_temp(ctx, content, delay=self.STATUS_DISPLAY, **kwargs)

    async def send_to_channel_temp(
        self,
        channel: discord.TextChannel,
        content: str = None,
        delay: float = ACK,
        **kwargs
    ) -> discord.Message:
        """Send a message to a channel and schedule it for deletion."""
        msg = await channel.send(content, **kwargs)
        self.schedule_delete(msg, delay)
        return msg

    def track_now_playing(self, guild_id: int, message: discord.Message):
        """Track a now playing message for later deletion."""
        # Delete the previous now playing message if it exists
        old_msg = self.now_playing_messages.get(guild_id)
        if old_msg:
            asyncio.create_task(self._safe_delete(old_msg))
        self.now_playing_messages[guild_id] = message

    async def _safe_delete(self, message: discord.Message):
        """Safely delete a message, ignoring errors."""
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

    def clear_now_playing(self, guild_id: int):
        """Delete and clear the now playing message for a guild."""
        msg = self.now_playing_messages.pop(guild_id, None)
        if msg:
            asyncio.create_task(self._safe_delete(msg))


# Global cleanup instance
cleanup = MessageCleanup()

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Cogs to load
COGS = ["music", "sports"]


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
    if message.author.bot:
        return

    logger.debug(
        f"MESSAGE RECEIVED: Guild={message.guild.name if message.guild else 'DM'} | "
        f"Channel=#{message.channel.name if hasattr(message.channel, 'name') else 'DM'} | "
        f"Author={message.author} | Content={message.content!r}"
    )

    if message.content.startswith("!"):
        logger.info(f"COMMAND DETECTED: {message.content}")

    await bot.process_commands(message)


@bot.event
async def on_command(ctx: commands.Context):
    """Log when a command is invoked and schedule user message for deletion."""
    logger.info(f"COMMAND INVOKED: {ctx.command.name} by {ctx.author} in #{ctx.channel.name}")
    # Delete user's command message after 20 seconds
    cleanup.schedule_delete(ctx.message, MessageCleanup.USER_COMMAND)


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    """Log command errors."""
    logger.error(f"COMMAND ERROR: {ctx.command.name if ctx.command else 'Unknown'} - {error}")
    if isinstance(error, commands.CommandNotFound):
        logger.warning(f"Command not found: {ctx.message.content}")
    elif isinstance(error, commands.MissingRequiredArgument):
        await cleanup.send_error(ctx, f"Missing argument: {error.param.name}")
    else:
        await cleanup.send_error(ctx, f"Error: {error}")


@bot.command(name="ping")
async def ping(ctx: commands.Context):
    """Simple test command to check if bot is responding."""
    logger.info("Ping command executed!")
    await cleanup.send_ack(ctx, f"Pong! Latency: {round(bot.latency * 1000)}ms")


async def load_cogs():
    """Load all cogs."""
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            logger.info(f"Loaded cog: {cog}")
        except Exception as e:
            logger.error(f"Failed to load cog {cog}: {e}")


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN not set in .env file")
        exit(1)

    async with bot:
        await load_cogs()
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
