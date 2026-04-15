"""Subreddit source — monitors any subreddit via RSS (default) or OAuth API."""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timezone

import aiohttp
import feedparser

from social_monitor.models import Post
from social_monitor.sources import register_source
from social_monitor.sources.base import AccessMethod, BaseSource, ConfigField

logger = logging.getLogger(__name__)


@register_source("subreddit")
class SubredditSource(BaseSource):
    name = "subreddit"
    display_name = "Subreddit"
    description = "Monitor one or more subreddits for new posts."
    default_interval = 120

    @classmethod
    def common_fields(cls) -> list[ConfigField]:
        return [
            ConfigField(
                key="subreddit",
                label="Subreddit",
                field_type="str",
                placeholder="Subreddit name (without r/)",
                required=True,
            ),
        ]

    @classmethod
    def supported_methods(cls) -> list[AccessMethod]:
        return [
            AccessMethod(
                key="rss",
                label="RSS Feed",
                description="No account or API key needed. Recommended for most users.",
                fields=[],
            ),
            AccessMethod(
                key="oauth",
                label="OAuth API",
                description="Higher rate limits and richer metadata. Requires a Reddit app.",
                fields=[
                    ConfigField(key="client_id", label="Client ID", field_type="str",
                                placeholder="Reddit app client ID",
                                help_text="Create at reddit.com/prefs/apps (script type)", required=True),
                    ConfigField(key="client_secret", label="Client Secret", field_type="password",
                                placeholder="Reddit app client secret", required=True),
                    ConfigField(key="user_agent", label="User Agent", field_type="str",
                                default="SocialMonitor/1.0"),
                ],
            ),
        ]

    def __init__(self):
        self._reddit = None
        self._subreddits: list[str] = []
        self._method: str = "rss"
        self._last_seen_ids: dict[str, set[str]] = {}

    async def setup(self, config: dict) -> None:
        settings = config.get("settings", {})
        # Support single "subreddit" field or legacy "subreddits" list
        single = settings.get("subreddit", "")
        legacy = settings.get("subreddits", [])
        if single:
            self._subreddits = [single]
        elif legacy:
            self._subreddits = legacy if isinstance(legacy, list) else [legacy]
        else:
            self._subreddits = []
        self._method = config.get("method", "rss")

        if self._method == "oauth":
            client_id = settings.get("client_id", "")
            client_secret = settings.get("client_secret", "")
            if client_id and client_secret:
                try:
                    import asyncpraw
                    self._reddit = asyncpraw.Reddit(
                        client_id=client_id, client_secret=client_secret,
                        user_agent=settings.get("user_agent", "SocialMonitor/1.0"),
                    )
                    logger.info("Subreddit source using OAuth API")
                except ImportError:
                    logger.warning("asyncpraw not installed — using RSS")
                    self._method = "rss"
            else:
                self._method = "rss"

        if self._method == "rss":
            logger.info("Subreddit source using RSS feeds")

        for sub in self._subreddits:
            self._last_seen_ids[sub] = set()

    async def fetch_new(self) -> list[Post]:
        if self._method == "oauth" and self._reddit:
            return await self._fetch_oauth()
        return await self._fetch_rss()

    async def _fetch_rss(self) -> list[Post]:
        posts: list[Post] = []
        async with aiohttp.ClientSession() as session:
            for sub_name in self._subreddits:
                url = f"https://www.reddit.com/r/{sub_name}/new/.rss"
                try:
                    async with session.get(url, headers={"User-Agent": "SocialMonitor/1.0"},
                                           timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            logger.warning("Reddit RSS r/%s returned %d", sub_name, resp.status)
                            continue
                        text = await resp.text()
                    feed = feedparser.parse(text)
                    for entry in feed.entries:
                        entry_id = entry.get("id", entry.get("link", ""))
                        if entry_id in self._last_seen_ids[sub_name]:
                            continue
                        ts = None
                        if hasattr(entry, "published_parsed") and entry.published_parsed:
                            ts = datetime.fromtimestamp(calendar.timegm(entry.published_parsed), tz=timezone.utc)

                        # Fetch full post body via Reddit's public JSON API
                        post_url = entry.get("link", "")
                        full_body = await self._fetch_full_body(session, post_url)
                        body = full_body if full_body else entry.get("summary", "")

                        posts.append(Post(
                            source="subreddit", post_id=f"reddit_{entry_id}",
                            title=entry.get("title", ""), body=body,
                            author=entry.get("author", ""), url=post_url,
                            timestamp=ts or datetime.now(timezone.utc),
                            metadata={"subreddit": sub_name, "method": "rss"},
                        ))
                        self._last_seen_ids[sub_name].add(entry_id)
                    self._trim_seen(sub_name)
                except Exception:
                    logger.exception("Error fetching RSS for r/%s", sub_name)
        return posts

    async def _fetch_full_body(self, session: aiohttp.ClientSession, post_url: str) -> str | None:
        """Fetch the full post selftext via Reddit's public JSON endpoint."""
        import re as _re
        match = _re.search(r"/comments/([a-z0-9]+)", post_url)
        if not match:
            return None
        post_id = match.group(1)
        json_url = f"https://www.reddit.com/comments/{post_id}.json"
        try:
            async with session.get(json_url, headers={"User-Agent": "SocialMonitor/1.0"},
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                selftext = data[0]["data"]["children"][0]["data"].get("selftext", "")
                return selftext if selftext else None
        except Exception:
            logger.debug("Failed to fetch full body for %s", post_url)
            return None

    async def _fetch_oauth(self) -> list[Post]:
        posts: list[Post] = []
        for sub_name in self._subreddits:
            try:
                subreddit = await self._reddit.subreddit(sub_name)
                async for submission in subreddit.new(limit=25):
                    sid = submission.id
                    if sid in self._last_seen_ids[sub_name]:
                        continue
                    posts.append(Post(
                        source="subreddit", post_id=f"reddit_{sid}",
                        title=submission.title, body=submission.selftext or "",
                        author=str(submission.author) if submission.author else "[deleted]",
                        url=f"https://reddit.com{submission.permalink}",
                        timestamp=datetime.fromtimestamp(submission.created_utc, tz=timezone.utc),
                        metadata={"subreddit": sub_name, "method": "oauth"},
                    ))
                    self._last_seen_ids[sub_name].add(sid)
                self._trim_seen(sub_name)
            except Exception:
                logger.exception("Error fetching r/%s via OAuth", sub_name)
        return posts

    def _trim_seen(self, sub_name: str) -> None:
        if len(self._last_seen_ids[sub_name]) > 500:
            self._last_seen_ids[sub_name] = set(list(self._last_seen_ids[sub_name])[-200:])

    async def teardown(self) -> None:
        if self._reddit:
            await self._reddit.close()
