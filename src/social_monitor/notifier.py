"""Windows toast notifications via winotify."""

from __future__ import annotations

import logging
import webbrowser
from pathlib import Path

from social_monitor.models import ScoredPost

logger = logging.getLogger(__name__)

# Limit how many individual notifications we send in a burst
MAX_INDIVIDUAL_NOTIFICATIONS = 3


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
            logger.warning(
                "winotify not installed — notifications will be logged only"
            )

    def notify(self, scored_posts: list[ScoredPost]) -> None:
        """Send desktop notifications for scored posts."""
        if not scored_posts:
            return

        if not self._winotify_available:
            for sp in scored_posts:
                logger.info(
                    "MATCH [%.2f] %s: %s — %s",
                    sp.score,
                    sp.post.source,
                    sp.post.title,
                    sp.post.url,
                )
            return

        if len(scored_posts) <= MAX_INDIVIDUAL_NOTIFICATIONS:
            for sp in scored_posts:
                self._send_single(sp)
        else:
            # Send a summary notification instead of spamming
            self._send_summary(scored_posts)

    def _send_single(self, sp: ScoredPost) -> None:
        from winotify import Notification, audio

        source_label = sp.post.source.replace("_", " ").title()
        title = f"[{source_label}] {sp.post.title[:80]}"
        body = sp.explanation or f"Relevance: {sp.score:.0%}"
        if sp.post.author:
            body += f" — by {sp.post.author}"

        toast = Notification(
            app_id=self._app_id,
            title=title,
            msg=body,
            icon="",  # Could point to resources/icon.png
        )

        if self._sound:
            toast.set_audio(audio.Default, loop=False)

        # Add action button to open the post URL
        toast.add_actions(label="Open Post", launch=sp.post.url)

        try:
            toast.show()
        except Exception:
            logger.exception("Failed to show notification for %s", sp.post.post_id)

    def _send_summary(self, scored_posts: list[ScoredPost]) -> None:
        from winotify import Notification, audio

        title = f"{len(scored_posts)} new relevant posts found"
        sources = set(sp.post.source for sp in scored_posts)
        body = f"From: {', '.join(s.replace('_', ' ').title() for s in sources)}"

        toast = Notification(
            app_id=self._app_id,
            title=title,
            msg=body,
            icon="",
        )

        if self._sound:
            toast.set_audio(audio.Default, loop=False)

        # Open the highest-scored post URL
        best = max(scored_posts, key=lambda sp: sp.score)
        toast.add_actions(label="Open Top Match", launch=best.post.url)

        try:
            toast.show()
        except Exception:
            logger.exception("Failed to show summary notification")
