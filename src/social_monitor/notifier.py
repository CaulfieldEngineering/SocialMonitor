"""Windows toast notifications via winotify."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from social_monitor.models import ScoredPost

logger = logging.getLogger(__name__)

MAX_INDIVIDUAL_NOTIFICATIONS = 3


def _get_exe_path() -> str:
    """Get the path to the running exe (or python script) for 'Open Panel' button."""
    if getattr(sys, 'frozen', False):
        return sys.executable
    # Running as script — launch via python -m
    return f"{sys.executable} -m social_monitor"


class Notifier:
    """Sends Windows toast notifications for matched posts."""

    def __init__(self, app_id: str = "SocialMonitor", sound: bool = True):
        self._app_id = app_id
        self._sound = sound
        self._winotify_available = False

        try:
            import winotify  # noqa: F401
            self._winotify_available = True
        except ImportError:
            logger.warning("winotify not installed — notifications will be logged only")

    def notify(self, scored_posts: list[ScoredPost]) -> None:
        if not scored_posts:
            return

        if not self._winotify_available:
            for sp in scored_posts:
                logger.info("MATCH [%.2f] %s: %s — %s", sp.score, sp.post.source, sp.post.title, sp.post.url)
            return

        if len(scored_posts) <= MAX_INDIVIDUAL_NOTIFICATIONS:
            for sp in scored_posts:
                self._send_single(sp)
        else:
            self._send_summary(scored_posts)

    def _send_single(self, sp: ScoredPost) -> None:
        from winotify import Notification, audio

        source_name = sp.post.metadata.get("source_name", sp.post.source.replace("_", " ").title())
        title = f"[{source_name}] {sp.post.title[:80]}"
        body = f"{sp.score:.0%} — {sp.explanation}" if sp.explanation else f"Relevance: {sp.score:.0%}"
        if sp.post.author:
            body += f"\nby {sp.post.author}"

        toast = Notification(app_id=self._app_id, title=title, msg=body, icon="")

        if self._sound:
            toast.set_audio(audio.Default, loop=False)

        toast.add_actions(label="Open Post", launch=sp.post.url)
        toast.add_actions(label="Open Panel", launch=_get_exe_path())

        try:
            toast.show()
        except Exception:
            logger.exception("Failed to show notification for %s", sp.post.post_id)

    def _send_summary(self, scored_posts: list[ScoredPost]) -> None:
        from winotify import Notification, audio

        title = f"{len(scored_posts)} new relevant posts found"
        sources = set(
            sp.post.metadata.get("source_name", sp.post.source.replace("_", " ").title())
            for sp in scored_posts
        )
        body = f"From: {', '.join(sources)}"

        toast = Notification(app_id=self._app_id, title=title, msg=body, icon="")

        if self._sound:
            toast.set_audio(audio.Default, loop=False)

        best = max(scored_posts, key=lambda sp: sp.score)
        toast.add_actions(label="Open Top Match", launch=best.post.url)
        toast.add_actions(label="Open Panel", launch=_get_exe_path())

        try:
            toast.show()
        except Exception:
            logger.exception("Failed to show summary notification")
