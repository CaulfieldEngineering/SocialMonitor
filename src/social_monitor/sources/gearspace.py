"""vBulletin Forum source — monitors any vBulletin-powered forum via RSS, scrape, or Playwright."""

from __future__ import annotations

import calendar
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup

from social_monitor.models import Post
from social_monitor.sources import register_source
from social_monitor.sources.base import AccessMethod, BaseSource, ConfigField

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _parse_base_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


@register_source("vbulletin_forum")
class VBulletinForumSource(BaseSource):
    name = "vbulletin_forum"
    display_name = "Web Forum (vBulletin)"
    description = "Monitor any vBulletin forum (GearSpace, etc.) via RSS, scrape, or headless browser."
    default_interval = 600

    @classmethod
    def common_fields(cls) -> list[ConfigField]:
        return [
            ConfigField(
                key="forum_urls",
                label="Forum Page URLs",
                field_type="str_list",
                placeholder="https://gearspace.com/board/music-computers/",
                required=True,
                help_text="Full URLs to the forum pages you want to monitor.",
            ),
        ]

    @classmethod
    def supported_methods(cls) -> list[AccessMethod]:
        return [
            AccessMethod(
                key="rss",
                label="RSS Feed",
                description="Tries the vBulletin RSS endpoint. May not work if the site has it disabled.",
                fields=[],
            ),
            AccessMethod(
                key="scrape",
                label="Web Scrape (HTTP)",
                description="Scrapes forum HTML with a browser-like request.",
                fields=[
                    ConfigField(key="session_cookie", label="Session Cookie", field_type="password",
                                placeholder="Optional: paste from browser DevTools",
                                help_text="If blocked, paste your browser cookie here."),
                ],
            ),
            AccessMethod(
                key="playwright",
                label="Web Scrape (Headless Browser)",
                description="Uses a real browser to bypass anti-bot. Requires: pip install 'social-monitor[scraping]'",
                fields=[],
            ),
        ]

    def __init__(self):
        self._method: str = "rss"
        self._forum_urls: list[str] = []
        self._session_cookie: str = ""
        self._seen_ids: set[str] = set()

    async def setup(self, config: dict) -> None:
        settings = config.get("settings", {})
        self._method = config.get("method", "rss")
        self._forum_urls = settings.get("forum_urls", [])
        self._session_cookie = settings.get("session_cookie", "")
        logger.info("vBulletin forum source initialized (method=%s, %d forums)", self._method, len(self._forum_urls))

    async def fetch_new(self) -> list[Post]:
        posts: list[Post] = []
        for url in self._forum_urls:
            try:
                if self._method == "rss":
                    posts.extend(await self._fetch_rss(url))
                elif self._method == "scrape":
                    posts.extend(await self._fetch_scrape(url))
                elif self._method == "playwright":
                    posts.extend(await self._fetch_playwright(url))
            except Exception:
                logger.exception("Error fetching vBulletin %s", url)
        self._trim_seen()
        return posts

    async def _fetch_rss(self, forum_url: str) -> list[Post]:
        import feedparser as fp
        base_url = _parse_base_url(forum_url)
        forum_id = None
        match = re.search(r"/board/[^/]+/(\d+)", forum_url) or re.search(r"f=(\d+)", forum_url)
        if match:
            forum_id = match.group(1)

        rss_urls = [f"{base_url}/external.php?type=RSS2"]
        if forum_id:
            rss_urls.insert(0, f"{base_url}/external.php?type=RSS2&forumids={forum_id}")

        async with aiohttp.ClientSession() as session:
            for rss_url in rss_urls:
                try:
                    async with session.get(rss_url, headers={"User-Agent": BROWSER_UA},
                                           timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            continue
                        text = await resp.text()
                    feed = fp.parse(text)
                    if feed.entries:
                        return self._parse_feed(feed.entries)
                except Exception:
                    pass
        logger.warning("vBulletin RSS unavailable for %s", forum_url)
        return []

    async def _fetch_scrape(self, forum_url: str) -> list[Post]:
        cookies = {}
        if self._session_cookie:
            for part in self._session_cookie.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    cookies[k.strip()] = v.strip()
        async with aiohttp.ClientSession(cookies=cookies) as session:
            try:
                async with session.get(forum_url, headers=BROWSER_HEADERS,
                                       timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status != 200:
                        return []
                    html = await resp.text()
                return self._parse_html(html, forum_url)
            except Exception:
                return []

    async def _fetch_playwright(self, forum_url: str) -> list[Post]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("Playwright not installed")
            return []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(user_agent=BROWSER_UA)
                await page.goto(forum_url, wait_until="domcontentloaded", timeout=30000)
                html = await page.content()
                await browser.close()
            return self._parse_html(html, forum_url)
        except Exception:
            logger.exception("Playwright failed for %s", forum_url)
            return []

    def _parse_feed(self, entries) -> list[Post]:
        posts: list[Post] = []
        for entry in entries:
            eid = entry.get("id", entry.get("link", ""))
            if eid in self._seen_ids:
                continue
            ts = None
            for attr in ("published_parsed", "updated_parsed"):
                parsed = getattr(entry, attr, None)
                if parsed:
                    ts = datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
                    break
            posts.append(Post(
                source="vbulletin_forum", post_id=f"vb_{eid}",
                title=entry.get("title", ""), body=entry.get("summary", ""),
                author=entry.get("author", ""), url=entry.get("link", ""),
                timestamp=ts or datetime.now(timezone.utc), metadata={"method": "rss"},
            ))
            self._seen_ids.add(eid)
        return posts

    def _parse_html(self, html: str, forum_url: str) -> list[Post]:
        soup = BeautifulSoup(html, "html.parser")
        threads = soup.select(
            "#threads .threadtitle a, .threadbit .title a, "
            "a[id^='thread_title_'], .threadlist .thread-title a"
        )
        if not threads:
            return []
        posts: list[Post] = []
        base = _parse_base_url(forum_url)
        for link in threads[:25]:
            href = link.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = base + href
            if href in self._seen_ids:
                continue
            author = ""
            row = link.find_parent("li") or link.find_parent("tr")
            if row:
                a_el = row.select_one(".author a, .username, .posterdate a")
                if a_el:
                    author = a_el.get_text(strip=True)
            posts.append(Post(
                source="vbulletin_forum", post_id=f"vb_{href}",
                title=link.get_text(strip=True), body="", author=author, url=href,
                timestamp=datetime.now(timezone.utc), metadata={"method": self._method},
            ))
            self._seen_ids.add(href)
        return posts

    def _trim_seen(self) -> None:
        if len(self._seen_ids) > 500:
            self._seen_ids = set(list(self._seen_ids)[-200:])

    async def teardown(self) -> None:
        pass
