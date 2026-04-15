"""Shared data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Post:
    """A post fetched from any source."""

    source: str  # "reddit", "kvr", "gearspace", "stackoverflow", "discord"
    post_id: str  # Unique within that source
    title: str
    body: str  # Plain text or markdown snippet
    author: str
    url: str
    timestamp: datetime
    metadata: dict = field(default_factory=dict)

    @property
    def text_for_scoring(self) -> str:
        """Combined text used for keyword matching and AI scoring."""
        parts = [self.title]
        if self.body:
            # Truncate body to ~500 chars for scoring efficiency
            parts.append(self.body[:500])
        return "\n".join(parts)


@dataclass
class ScoredPost:
    """A post with AI or keyword relevance score."""

    post: Post
    score: float  # 0.0 - 1.0
    explanation: str  # One-line rationale
