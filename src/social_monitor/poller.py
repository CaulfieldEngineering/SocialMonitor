"""Async polling orchestrator — fetches, deduplicates, scores, and notifies."""

from __future__ import annotations

import asyncio
import logging

from social_monitor.config import AppConfig, SourceInstanceConfig, get_effective_keywords
from social_monitor.database import Database
from social_monitor.models import Post, ScoredPost
from social_monitor.notifier import Notifier
from social_monitor.sources.base import BaseSource

logger = logging.getLogger(__name__)


def _import_all_sources() -> None:
    """Import all built-in source modules to trigger @register_source decorators."""
    import social_monitor.sources.reddit  # noqa: F401  (subreddit)
    import social_monitor.sources.kvr_audio  # noqa: F401  (phpbb_forum)
    import social_monitor.sources.gearspace  # noqa: F401  (vbulletin_forum)
    import social_monitor.sources.stackoverflow  # noqa: F401  (stackexchange)
    import social_monitor.sources.discord_bot  # noqa: F401  (discord)
    import social_monitor.sources.rss_feed  # noqa: F401  (rss_feed)


class Poller:
    """Orchestrates async polling of all enabled sources."""

    def __init__(
        self,
        config: AppConfig,
        db: Database,
        notifier: Notifier,
        scorer=None,
        signal_bridge=None,
    ):
        self.config = config
        self.db = db
        self.notifier = notifier
        self.scorer = scorer
        self.signals = signal_bridge  # Optional SignalBridge for UI updates
        self._active_sources: list[tuple[SourceInstanceConfig, BaseSource]] = []
        self._scoring_queue: asyncio.Queue[Post] = asyncio.Queue()
        self._stopped = False
        self._paused = False
        self._check_now_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def setup_sources(self) -> None:
        """Initialize all enabled source instances from config."""
        from social_monitor.sources import SOURCE_REGISTRY

        _import_all_sources()

        for src_cfg in self.config.sources:
            if not src_cfg.enabled:
                logger.debug("Source '%s' is disabled, skipping", src_cfg.name)
                continue

            source_cls = SOURCE_REGISTRY.get(src_cfg.type)
            if source_cls is None:
                logger.warning(
                    "Source '%s' has unknown type '%s' — available: %s",
                    src_cfg.name, src_cfg.type, list(SOURCE_REGISTRY.keys()),
                )
                continue

            # Build the config dict that the source plugin expects
            plugin_config = {
                "method": src_cfg.method or source_cls.default_method(),
                "keywords": get_effective_keywords(
                    src_cfg.keywords, self.config.global_keywords
                ),
                "settings": src_cfg.settings,
            }

            source = source_cls()
            errors = source.validate_config(plugin_config)
            if errors:
                logger.warning("Source '%s' has config errors: %s", src_cfg.name, "; ".join(errors))
                continue

            try:
                await source.setup(plugin_config)
                self._active_sources.append((src_cfg, source))
                logger.info("Source '%s' (%s) initialized", src_cfg.name, src_cfg.type)
            except Exception:
                logger.exception("Failed to initialize source '%s'", src_cfg.name)

    async def run(self) -> None:
        """Start polling all sources and the scoring consumer."""
        await self.setup_sources()

        if not self._active_sources:
            logger.warning("No sources enabled or initialized — nothing to poll")
            return

        for src_cfg, source in self._active_sources:
            task = asyncio.create_task(
                self._poll_source(src_cfg, source),
                name=f"poll_{src_cfg.name}",
            )
            self._tasks.append(task)

        task = asyncio.create_task(self._scoring_consumer(), name="scoring_consumer")
        self._tasks.append(task)

        logger.info("Polling started for %d source(s)", len(self._active_sources))
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def stop(self) -> None:
        self._stopped = True
        for task in self._tasks:
            task.cancel()

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def check_now(self) -> None:
        """Wake all source poll loops immediately."""
        self._check_now_event.set()
        if self.signals:
            self.signals.source_status.emit("Checking all sources...", 0)
        logger.info("Check Now triggered — waking all sources")
        # Clear after a short delay so all source loops see the event
        async def _clear_later():
            await asyncio.sleep(1)
            self._check_now_event.clear()
        try:
            asyncio.ensure_future(_clear_later())
        except RuntimeError:
            pass

    async def _poll_source(self, src_cfg: SourceInstanceConfig, source: BaseSource) -> None:
        """Poll a single source instance on its configured interval."""
        interval = src_cfg.interval
        await asyncio.sleep(2)  # Stagger startup

        while not self._stopped:
            if not self._paused:
                try:
                    posts = await source.fetch_new()
                    new_count = 0
                    for post in posts:
                        post.metadata["source_name"] = src_cfg.name
                        if not await self.db.is_seen(post.source, post.post_id):
                            await self._scoring_queue.put(post)
                            new_count += 1
                    if self.signals:
                        if new_count:
                            self.signals.source_status.emit(src_cfg.name, new_count)
                        else:
                            self.signals.source_status.emit(f"{src_cfg.name}: no new posts", 0)
                    if new_count:
                        logger.info("Source '%s': %d new post(s) queued", src_cfg.name, new_count)
                    else:
                        logger.debug("Source '%s': polled, 0 new", src_cfg.name)
                except Exception:
                    logger.exception("Error polling source '%s'", src_cfg.name)
                    if self.signals:
                        self.signals.source_status.emit(f"{src_cfg.name}: error", 0)

            # Sleep until interval expires OR check_now wakes us
            try:
                await asyncio.wait_for(self._check_now_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _scoring_consumer(self) -> None:
        """Batch posts from the queue, score them, and send notifications."""
        batch: list[Post] = []
        batch_interval = 10

        while not self._stopped:
            try:
                post = await asyncio.wait_for(self._scoring_queue.get(), timeout=batch_interval)
                batch.append(post)
            except asyncio.TimeoutError:
                pass

            while not self._scoring_queue.empty() and len(batch) < 10:
                try:
                    batch.append(self._scoring_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            if not batch:
                continue

            use_ai = self.scorer and self.config.ai.provider != "none"

            if use_ai:
                if self.signals:
                    self.signals.ai_status.emit(f"AI scoring {len(batch)} post(s)...")
                scored = await self.scorer.score_batch(
                    batch, self.config.global_keywords, self.config.ai.interests
                )
                scoring_method = "ai"
                # Check if scoring had errors (all posts got 0.5 with error explanation)
                has_errors = any("AI error:" in sp.explanation for sp in scored)
                if self.signals:
                    if has_errors:
                        err = next(sp.explanation for sp in scored if "AI error:" in sp.explanation)
                        self.signals.ai_status.emit(f"AI ERROR: {err}")
                    else:
                        self.signals.ai_status.emit(
                            f"{self.scorer.status_text()} | Threshold: {self.config.ai.threshold:.0%}"
                        )
            else:
                scored = self._keyword_score(batch)
                scoring_method = "keyword"

            threshold = self.config.ai.threshold
            to_notify: list[ScoredPost] = []

            for sp in scored:
                if self._matches_negative(sp.post):
                    await self.db.mark_seen(sp.post)
                    # Still emit to feed so user sees filtered posts
                    if self.signals:
                        from social_monitor.ui.signals import JsonScoredPost
                        self.signals.post_scored.emit(
                            JsonScoredPost(sp, f"FILTERED (negative keyword) | {sp.explanation}")
                        )
                    continue

                await self.db.save_scored(sp, notified=sp.score >= threshold)

                # Build trigger info string
                if scoring_method == "ai":
                    trigger = f"AI scored: {sp.score:.0%} | {sp.explanation}"
                else:
                    trigger = f"Keyword match: {sp.score:.0%} | {sp.explanation}"

                if sp.score >= threshold:
                    trigger += " | NOTIFIED"
                    to_notify.append(sp)

                # Emit to feed
                if self.signals:
                    from social_monitor.ui.signals import JsonScoredPost
                    self.signals.post_scored.emit(JsonScoredPost(sp, trigger))

            if to_notify:
                self.notifier.notify(to_notify)
                logger.info("Notified user about %d post(s) above threshold", len(to_notify))

            batch.clear()

    def _keyword_score(self, posts: list[Post]) -> list[ScoredPost]:
        keywords = [kw.lower() for kw in self.config.global_keywords]
        scored: list[ScoredPost] = []
        for post in posts:
            text = post.text_for_scoring.lower()
            matches = [kw for kw in keywords if kw in text]
            if matches:
                score = min(len(matches) / max(len(keywords), 1) * 2, 1.0)
                title_lower = post.title.lower()
                title_matches = [kw for kw in keywords if kw in title_lower]
                if title_matches:
                    score = min(score + 0.2, 1.0)
                explanation = f"Keyword match: {', '.join(matches[:3])}"
            else:
                score = 0.1
                explanation = "No keyword match"
            scored.append(ScoredPost(post=post, score=score, explanation=explanation))
        return scored

    def _matches_negative(self, post: Post) -> bool:
        if not self.config.negative_keywords:
            return False
        text = post.text_for_scoring.lower()
        return any(nk.lower() in text for nk in self.config.negative_keywords)

    async def teardown(self) -> None:
        for src_cfg, source in self._active_sources:
            try:
                await source.teardown()
            except Exception:
                logger.exception("Error tearing down source '%s'", src_cfg.name)
