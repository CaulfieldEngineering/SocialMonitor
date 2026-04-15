"""phpBB Forum source — monitors any phpBB-based forum via Atom feed or scrape."""

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


@register_source("phpbb_forum")
class PhpBBForumSource(BaseSource):
    name = "phpbb_forum"
    display_name = "Web Forum (phpBB)"
    description = "Monitor any phpBB-powered forum (KVR, etc.) via Atom feed or scrape."
    default_interval = 300

    @classmethod
    def common_fields(cls) -> list[ConfigField]:
        return [
            ConfigField(
                key="base_url",
                label="Forum Base URL",
                field_type="str",
                placeholder="https://www.kvraudio.com/forum",
                required=True,
                help_text="The root URL of the phpBB forum (no trailing slash).",
            ),
        ]

    @classmethod
    def supported_methods(cls) -> list[AccessMethod]:
        return [
            AccessMethod(
                key="rss",
                label="Atom Feed",
                description="Uses phpBB's built-in Atom feed at /app.php/feed. Reliable and fast.",
                fields=[
                    ConfigField(
                        key="forum_ids",
                        label="Forum IDs (optional)",
                        field_type="int_list",
                        placeholder="Forum ID number",
                        help_text="Leave empty for the global feed, or add specific sub-forum IDs.",
                    ),
                ],
            ),
            AccessMethod(
                key="scrape",
                label="Web Scrape",
                description="Scrapes the forum HTML directly. Use if the Atom feed is unavailable.",
                fields=[
                    ConfigField(
                        key="forum_paths",
                        label="Forum Paths",
                        field_type="str_list",
                        placeholder="/viewforum.php?f=1",
                        required=True,
                        help_text="Relative paths to sub-forums to monitor.",
                    ),
                ],
            ),
        ]

    def __init__(self):
        self._method: str = "rss"
        self._base_url: str = ""
        self._forum_ids: list[int] = []
        self._forum_paths: list[str] = []
        self._seen_ids: set[str] = set()

    async def setup(self, config: dict) -> None:
        settings = config.get("settings", {})
        self._method = config.get("method", "rss")
        self._base_url = settings.get("base_url", "").rstrip("/")
        self._forum_ids = settings.get("forum_ids", [])
        self._forum_paths = settings.get("forum_paths", [])
        logger.info("phpBB forum source initialized: %s (method=%s)", self._base_url, self._method)

    async def fetch_new(self) -> list[Post]:
        if self._method == "scrape":
            return await self._fetch_scrape()
        return await self._fetch_rss()

    async def _fetch_rss(self) -> list[Post]:
        posts: list[Post] = []
        feed_base = f"{self._base_url}/app.php/feed"
        feed_urls = []
        if self._forum_ids:
            for fid in self._forum_ids:
                feed_urls.append(f"{feed_base}/forum/{fid}")
        else:
            feed_urls.append(feed_base)

        async with aiohttp.ClientSession() as session:
            for url in feed_urls:
                try:
                    async with session.get(url, headers={"User-Agent": "SocialMonitor/1.0"},
                                           timeout=aiohttp.ClientTimeout(total=20)) as resp:
                        if resp.status != 200:
                            logger.warning("phpBB feed %s returned %d", url, resp.status)
                            continue
                        text = await resp.text()
                    feed = feedparser.parse(text)
                    for entry in feed.entries:
                        entry_id = entry.get("id", entry.get("link", ""))
                        if entry_id in self._seen_ids:
                            continue
                        ts = None
                        for attr in ("updated_parsed", "published_parsed"):
                            parsed = getattr(entry, attr, None)
                            if parsed:
                                ts = datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
                                break
                        author = ""
                        if hasattr(entry, "author_detail"):
                            author = entry.author_detail.get("name", "")
                        elif hasattr(entry, "author"):
                            author = entry.author
                        posts.append(Post(
                            source="phpbb_forum", post_id=f"phpbb_{entry_id}",
                            title=entry.get("title", ""), body=entry.get("summary", ""),
                            author=author, url=entry.get("link", ""),
                            timestamp=ts or datetime.now(timezone.utc),
                            metadata={"base_url": self._base_url, "method": "rss"},
                        ))
                        self._seen_ids.add(entry_id)
                except Exception:
                    logger.exception("Error fetching phpBB feed %s", url)

        self._trim_seen()
        return posts

    async def _fetch_scrape(self) -> list[Post]:
        from bs4 import BeautifulSoup
        posts: list[Post] = []
        async with aiohttp.ClientSession() as session:
            for path in self._forum_paths:
                url = f"{self._base_url}{path}"
                try:
                    async with session.get(url, headers={"User-Agent": "SocialMonitor/1.0"},
                                           timeout=aiohttp.ClientTimeout(total=20)) as resp:
                        if resp.status != 200:
                            continue
                        html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")
                    for topic in soup.select(".topictitle"):
                        link = topic.get("href", "")
                        if not link:
                            continue
                        if link.startswith("./"):
                            link = self._base_url + link[1:]
                        elif link.startswith("/"):
                            link = self._base_url + link
                        if link in self._seen_ids:
                            continue
                        posts.append(Post(
                            source="phpbb_forum", post_id=f"phpbb_{link}",
                            title=topic.get_text(strip=True), body="",
                            author="", url=link,
                            timestamp=datetime.now(timezone.utc),
                            metadata={"base_url": self._base_url, "method": "scrape"},
                        ))
                        self._seen_ids.add(link)
                except Exception:
                    logger.exception("Error scraping phpBB %s", url)
        self._trim_seen()
        return posts

    def _trim_seen(self) -> None:
        if len(self._seen_ids) > 500:
            self._seen_ids = set(list(self._seen_ids)[-200:])

    async def teardown(self) -> None:
        pass
