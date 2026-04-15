"""Generic RSS/Atom feed source — works with any website that has a feed URL."""

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


@register_source("rss_feed")
class RSSFeedSource(BaseSource):
    name = "rss_feed"
    display_name = "RSS / Atom Feed"
    description = "Monitor any website via its RSS or Atom feed URL."
    default_interval = 300

    @classmethod
    def supported_methods(cls) -> list[AccessMethod]:
        return [
            AccessMethod(
                key="rss",
                label="RSS / Atom Feed",
                description="Fetches and parses standard RSS or Atom feeds.",
                fields=[
                    ConfigField(
                        key="feed_urls",
                        label="Feed URLs",
                        field_type="str_list",
                        placeholder="https://example.com/feed.xml",
                        required=True,
                        help_text="Add one or more RSS/Atom feed URLs.",
                    ),
                ],
            ),
        ]

    def __init__(self):
        self._feed_urls: list[str] = []
        self._seen_ids: set[str] = set()

    async def setup(self, config: dict) -> None:
        settings = config.get("settings", {})
        self._feed_urls = settings.get("feed_urls", [])
        logger.info("RSS feed source initialized — %d feed(s)", len(self._feed_urls))

    async def fetch_new(self) -> list[Post]:
        posts: list[Post] = []
        async with aiohttp.ClientSession() as session:
            for url in self._feed_urls:
                try:
                    async with session.get(
                        url, headers={"User-Agent": "SocialMonitor/1.0"},
                        timeout=aiohttp.ClientTimeout(total=20),
                    ) as resp:
                        if resp.status != 200:
                            logger.warning("Feed %s returned %d", url, resp.status)
                            continue
                        text = await resp.text()

                    feed = feedparser.parse(text)
                    feed_title = feed.feed.get("title", url)

                    for entry in feed.entries:
                        entry_id = entry.get("id", entry.get("link", ""))
                        if entry_id in self._seen_ids:
                            continue
                        ts = None
                        for attr in ("published_parsed", "updated_parsed"):
                            parsed = getattr(entry, attr, None)
                            if parsed:
                                ts = datetime.fromtimestamp(
                                    calendar.timegm(parsed), tz=timezone.utc
                                )
                                break
                        author = ""
                        if hasattr(entry, "author_detail"):
                            author = entry.author_detail.get("name", "")
                        elif hasattr(entry, "author"):
                            author = entry.author
                        post = Post(
                            source="rss_feed", post_id=f"rss_{entry_id}",
                            title=entry.get("title", ""),
                            body=entry.get("summary", entry.get("description", "")),
                            author=author, url=entry.get("link", ""),
                            timestamp=ts or datetime.now(timezone.utc),
                            metadata={"feed_url": url, "feed_title": feed_title, "method": "rss"},
                        )
                        posts.append(post)
                        self._seen_ids.add(entry_id)
                except Exception:
                    logger.exception("Error fetching feed %s", url)

        if len(self._seen_ids) > 1000:
            excess = len(self._seen_ids) - 500
            self._seen_ids = set(list(self._seen_ids)[excess:])

        logger.debug("RSS feed: fetched %d new posts", len(posts))
        return posts

    async def teardown(self) -> None:
        pass
