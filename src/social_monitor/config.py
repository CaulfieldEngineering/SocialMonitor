"""Configuration loading and Pydantic models."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path(os.environ.get("APPDATA", "~")) / "SocialMonitor"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.yaml"


# ---------------------------------------------------------------------------
# Pydantic config models
# ---------------------------------------------------------------------------


class GeneralConfig(BaseModel):
    start_minimized: bool = True
    start_on_login: bool = False
    notification_sound: bool = True
    poll_interval: int = 120  # seconds, applies to all sources
    log_level: str = "INFO"


DEFAULT_AI_PROMPT = """\
You are a relevance scoring assistant. Your job is to score social media and forum \
posts for relevance to the user's interests.

The user is interested in:
{interests}

Their monitored keywords are: {keywords}

{filters}

For each post, assign a relevance score from 0.0 (completely irrelevant) to 1.0 \
(highly relevant and actionable).

Consider semantic relevance, not just keyword matching. For example:
- A post asking "how do I build a wavetable synth plugin?" is highly relevant to \
someone interested in "VST plugin development" even without those exact words.
- A post that is a question the user could answer scores higher than a general discussion.
- Posts where the user could provide unique expertise score highest.

Respond ONLY with a JSON array. Each element must have these exact keys:
- "id": the post ID string (exactly as provided)
- "score": a float from 0.0 to 1.0
- "explanation": a brief one-line explanation of the score

Example response:
[{{"id": "reddit_abc123", "score": 0.85, "explanation": "User asking about VST development in JUCE"}}]\
"""


class AIConfig(BaseModel):
    provider: Literal["claude", "openai", "openrouter", "none"] = "none"
    api_key: str = ""
    model: str = "claude-haiku-4-5-20251001"
    threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    interests: str = ""
    prompt: str = ""  # Empty = use DEFAULT_AI_PROMPT
    prefer_questions: bool = True  # Boost posts that are questions
    prefer_unanswered: bool = True  # Boost posts with 0 answers
    exclude_self_promo: bool = False  # Penalize self-promotion posts


class SourceInstanceConfig(BaseModel):
    """A single configured source instance.

    The `type` field maps to a registered source plugin name.
    The `name` is a user-chosen label (e.g., "My Reddit", "KVR Instruments").
    The `settings` dict holds type-specific configuration — its schema is
    defined by the source plugin's `config_fields()` method.
    """

    name: str = ""
    type: str = ""  # Must match a key in SOURCE_REGISTRY
    method: str = ""  # Access method key (e.g. "rss", "oauth", "scrape"). Empty = plugin default.
    enabled: bool = True
    interval: int = 120
    keywords: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)


class AppConfig(BaseModel):
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    global_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)
    sources: list[SourceInstanceConfig] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Load / save helpers
# ---------------------------------------------------------------------------


def get_config_path() -> Path:
    """Return the resolved config file path, creating the directory if needed."""
    path = DEFAULT_CONFIG_PATH.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_config(path: Path | None = None) -> AppConfig:
    """Load config from YAML, falling back to defaults for missing fields."""
    path = path or get_config_path()
    if not path.exists():
        logger.info("No config file found at %s — using defaults", path)
        return AppConfig()

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # Migrate old source type names
    _TYPE_MIGRATION = {
        "reddit": "subreddit",
        "kvr_audio": "phpbb_forum",
        "gearspace": "vbulletin_forum",
        "stackoverflow": "stackexchange",
    }
    for src in raw.get("sources", []):
        old_type = src.get("type", "")
        if old_type in _TYPE_MIGRATION:
            src["type"] = _TYPE_MIGRATION[old_type]
            logger.info("Migrated source type '%s' -> '%s'", old_type, src["type"])

    return AppConfig.model_validate(raw)


def save_config(config: AppConfig, path: Path | None = None) -> None:
    """Save config to YAML."""
    path = path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(mode="json")
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    logger.info("Config saved to %s", path)


def get_effective_keywords(
    source_keywords: list[str], global_keywords: list[str]
) -> list[str]:
    """Return source-specific keywords if set, otherwise global keywords."""
    return source_keywords if source_keywords else global_keywords
