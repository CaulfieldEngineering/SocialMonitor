"""Discord source — monitors channels in real-time via discord.py bot."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from social_monitor.models import Post
from social_monitor.sources import register_source
from social_monitor.sources.base import AccessMethod, BaseSource, ConfigField

logger = logging.getLogger(__name__)


@register_source("discord")
class DiscordSource(BaseSource):
    name = "discord"
    display_name = "Discord"
    description = "Monitor Discord channels in real-time via bot."
    default_interval = 60

    @classmethod
    def supported_methods(cls) -> list[AccessMethod]:
        return [
            AccessMethod(
                key="bot",
                label="Discord Bot",
                description="Connects via WebSocket for real-time messages. Requires MESSAGE_CONTENT intent in Developer Portal.",
                fields=[
                    ConfigField(
                        key="bot_token",
                        label="Bot Token",
                        field_type="password",
                        placeholder="Discord bot token from Developer Portal",
                        required=True,
                    ),
                    ConfigField(
                        key="channel_ids",
                        label="Channel IDs",
                        field_type="str_list",
                        placeholder="Channel ID (right-click channel > Copy ID)",
                        required=True,
                        help_text="Enable Developer Mode in Discord settings to copy IDs.",
                    ),
                ],
            ),
        ]

    def __init__(self):
        self._bot = None
        self._bot_task: asyncio.Task | None = None
        self._monitored_channels: set[int] = set()
        self._buffer: list[Post] = []
        self._buffer_lock = asyncio.Lock()

    async def setup(self, config: dict) -> None:
        settings = config.get("settings", {})
        bot_token = settings.get("bot_token", "")
        if not bot_token:
            raise ValueError("Discord bot token is required")

        for cid in settings.get("channel_ids", []):
            self._monitored_channels.add(int(cid))

        try:
            import discord

            intents = discord.Intents.default()
            intents.message_content = True
            self._bot = discord.Client(intents=intents)

            @self._bot.event
            async def on_ready():
                logger.info("Discord bot connected as %s, monitoring %d channel(s)",
                            self._bot.user, len(self._monitored_channels))

            @self._bot.event
            async def on_message(message: discord.Message):
                if message.author.bot:
                    return
                if message.channel.id not in self._monitored_channels:
                    return
                post = Post(
                    source="discord", post_id=f"discord_{message.id}",
                    title=f"#{message.channel.name}", body=message.content,
                    author=str(message.author), url=message.jump_url,
                    timestamp=message.created_at.replace(tzinfo=timezone.utc),
                    metadata={
                        "server_name": message.guild.name if message.guild else "",
                        "channel_name": message.channel.name,
                        "method": "bot",
                    },
                )
                async with self._buffer_lock:
                    self._buffer.append(post)

            self._bot_task = asyncio.create_task(self._run_bot(bot_token))
            logger.info("Discord source initialized")
        except ImportError:
            raise ImportError("discord.py is required: pip install discord.py")

    async def _run_bot(self, token: str) -> None:
        try:
            await self._bot.start(token)
        except Exception:
            logger.exception("Discord bot disconnected")

    async def fetch_new(self) -> list[Post]:
        async with self._buffer_lock:
            posts = list(self._buffer)
            self._buffer.clear()
        return posts

    async def teardown(self) -> None:
        if self._bot:
            await self._bot.close()
        if self._bot_task:
            self._bot_task.cancel()
