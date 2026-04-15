"""Stack Exchange source — monitors any Stack Exchange site (SO, SuperUser, etc.)."""

from __future__ import annotations

import calendar
import logging
import re
from datetime import datetime, timezone

import aiohttp

from social_monitor.models import Post
from social_monitor.sources import register_source
from social_monitor.sources.base import AccessMethod, BaseSource, ConfigField

logger = logging.getLogger(__name__)

SE_API_BASE = "https://api.stackexchange.com/2.3"


@register_source("stackexchange")
class StackExchangeSource(BaseSource):
    name = "stackexchange"
    display_name = "Stack Exchange"
    description = "Monitor questions on any Stack Exchange site (Stack Overflow, SuperUser, etc.)."
    default_interval = 300

    @classmethod
    def common_fields(cls) -> list[ConfigField]:
        return [
            ConfigField(key="site", label="Site", field_type="str", default="stackoverflow",
                        placeholder="stackoverflow",
                        help_text="SE site name: stackoverflow, superuser, askubuntu, etc."),
            ConfigField(key="tags", label="Tags", field_type="str_list",
                        placeholder="e.g. python, vst, juce",
                        help_text="At least one tag is recommended."),
        ]

    @classmethod
    def supported_methods(cls) -> list[AccessMethod]:
        return [
            AccessMethod(
                key="api", label="REST API",
                description="Uses the Stack Exchange API. 300 requests/day free, 10K with key.",
                fields=[ConfigField(key="api_key", label="API Key", field_type="password",
                                    placeholder="Optional — raises daily quota to 10,000")],
            ),
            AccessMethod(
                key="rss", label="RSS Feed",
                description="Uses Stack Exchange RSS feeds. No API key needed.",
                fields=[],
            ),
        ]

    def __init__(self):
        self._method: str = "api"
        self._site: str = "stackoverflow"
        self._api_key: str = ""
        self._tags: list[str] = []
        self._keywords: list[str] = []
        self._last_check: int = 0
        self._seen_ids: set[str] = set()

    async def setup(self, config: dict) -> None:
        settings = config.get("settings", {})
        self._method = config.get("method", "api")
        self._site = settings.get("site", "stackoverflow")
        self._api_key = settings.get("api_key", "")
        self._tags = settings.get("tags", [])
        self._keywords = config.get("keywords", [])
        self._last_check = int(datetime.now(timezone.utc).timestamp())
        logger.info("Stack Exchange source: %s (method=%s, tags=%s)",
                     self._site, self._method, ", ".join(self._tags) or "(none)")

    async def fetch_new(self) -> list[Post]:
        if self._method == "rss":
            return await self._fetch_rss()
        return await self._fetch_api()

    async def _fetch_api(self) -> list[Post]:
        posts: list[Post] = []
        params = {
            "order": "desc", "sort": "creation", "site": self._site,
            "filter": "withbody", "fromdate": self._last_check, "pagesize": 30,
        }
        if self._api_key:
            params["key"] = self._api_key
        if self._tags:
            params["tagged"] = ";".join(self._tags)
        if self._keywords:
            params["q"] = " ".join(self._keywords)

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{SE_API_BASE}/search/advanced", params=params,
                                       timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return posts
                    data = await resp.json()
                    for item in data.get("items", []):
                        qid = str(item["question_id"])
                        if qid in self._seen_ids:
                            continue
                        ts = datetime.fromtimestamp(item["creation_date"], tz=timezone.utc)
                        body_text = re.sub(r"<[^>]+>", "", item.get("body", ""))
                        posts.append(Post(
                            source="stackexchange", post_id=f"se_{qid}",
                            title=item.get("title", ""), body=body_text,
                            author=item.get("owner", {}).get("display_name", ""),
                            url=item.get("link", ""), timestamp=ts,
                            metadata={"site": self._site, "tags": item.get("tags", []), "method": "api"},
                        ))
                        self._seen_ids.add(qid)
                    if posts:
                        self._last_check = int(max(p.timestamp for p in posts).timestamp())
            except Exception:
                logger.exception("Error fetching from SE API")
        self._trim_seen()
        return posts

    async def _fetch_rss(self) -> list[Post]:
        import feedparser
        posts: list[Post] = []
        base = f"https://{self._site}.com" if "." not in self._site else f"https://{self._site}"
        feed_urls = [f"{base}/feeds/tag/{tag}" for tag in self._tags] if self._tags else [f"{base}/feeds"]

        async with aiohttp.ClientSession() as session:
            for url in feed_urls:
                try:
                    async with session.get(url, headers={"User-Agent": "SocialMonitor/1.0"},
                                           timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            continue
                        text = await resp.text()
                    feed = feedparser.parse(text)
                    for entry in feed.entries:
                        eid = entry.get("id", entry.get("link", ""))
                        if eid in self._seen_ids:
                            continue
                        ts = None
                        if hasattr(entry, "published_parsed") and entry.published_parsed:
                            ts = datetime.fromtimestamp(calendar.timegm(entry.published_parsed), tz=timezone.utc)
                        posts.append(Post(
                            source="stackexchange", post_id=f"se_rss_{eid}",
                            title=entry.get("title", ""), body=entry.get("summary", ""),
                            author=entry.get("author", ""), url=entry.get("link", ""),
                            timestamp=ts or datetime.now(timezone.utc),
                            metadata={"site": self._site, "method": "rss"},
                        ))
                        self._seen_ids.add(eid)
                except Exception:
                    logger.exception("Error fetching SE RSS %s", url)
        self._trim_seen()
        return posts

    def _trim_seen(self) -> None:
        if len(self._seen_ids) > 1000:
            self._seen_ids = set(list(self._seen_ids)[-500:])

    async def teardown(self) -> None:
        pass

    def validate_config(self, config: dict) -> list[str]:
        errors = []
        settings = config.get("settings", {})
        if not settings.get("tags") and not config.get("keywords"):
            errors.append("Stack Exchange: at least one tag or keyword is required")
        return errors
