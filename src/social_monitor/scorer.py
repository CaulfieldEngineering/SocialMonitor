"""AI relevance scoring engine — supports Claude, OpenAI, and OpenRouter."""

from __future__ import annotations

import json
import logging
from typing import Literal

from social_monitor.config import AIConfig, DEFAULT_AI_PROMPT
from social_monitor.models import Post, ScoredPost

logger = logging.getLogger(__name__)

USER_PROMPT_TEMPLATE = """\
Score the following {count} post(s) for relevance:

{posts_text}
"""

# Popular OpenRouter models (for UI dropdown)
OPENROUTER_MODELS = [
    # Anthropic (no Haiku on OpenRouter currently)
    ("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6 (cheapest Anthropic)"),
    ("anthropic/claude-opus-4.6", "Claude Opus 4.6"),
    # OpenAI
    ("openai/gpt-4o-mini", "GPT-4o Mini (cheapest OpenAI)"),
    ("openai/gpt-4o", "GPT-4o"),
    # Google
    ("google/gemini-2.5-flash", "Gemini 2.5 Flash (cheapest Google)"),
    ("google/gemini-2.5-pro", "Gemini 2.5 Pro"),
    # Kimi (MoonshotAI)
    ("moonshotai/kimi-k2.5", "Kimi K2.5 (MoonshotAI)"),
    # Meta
    ("meta-llama/llama-4-maverick", "Llama 4 Maverick"),
    ("meta-llama/llama-4-scout", "Llama 4 Scout"),
    # DeepSeek
    ("deepseek/deepseek-chat-v3", "DeepSeek V3 (very cheap)"),
    ("deepseek/deepseek-r1", "DeepSeek R1"),
    # Mistral
    ("mistralai/mistral-small-3.2", "Mistral Small 3.2"),
    # Qwen
    ("qwen/qwen3-30b-a3b", "Qwen3 30B (very cheap)"),
]


def _format_posts_for_scoring(posts: list[Post]) -> str:
    parts = []
    for post in posts:
        text = post.text_for_scoring
        source_label = post.source.replace("_", " ").title()
        parts.append(
            f"---\nID: {post.post_id}\nSource: {source_label}\n"
            f"Title: {post.title}\nBody: {text[:400]}\n"
        )
    return "\n".join(parts)


def _extract_json(text: str) -> str:
    """Extract JSON array from AI response, handling markdown fences, preamble text, etc."""
    import re
    text = text.strip()

    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    # If text doesn't start with [, try to find the JSON array in it
    if not text.startswith("["):
        # Look for the first [ and last ]
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

    return text


def _parse_scores(response_text: str, posts: list[Post]) -> list[ScoredPost]:
    text = _extract_json(response_text)

    try:
        scores_data = json.loads(text)
    except json.JSONDecodeError:
        logger.error("Failed to parse AI scoring response (%d chars): %s", len(text), text[:300])
        return [ScoredPost(post=p, score=0.5, explanation="AI parse error") for p in posts]

    if not isinstance(scores_data, list):
        logger.error("AI response is not a list: %s", type(scores_data))
        return [ScoredPost(post=p, score=0.5, explanation="AI parse error: not a list") for p in posts]

    post_map = {p.post_id: p for p in posts}
    scored: list[ScoredPost] = []

    for item in scores_data:
        if not isinstance(item, dict):
            continue
        pid = item.get("id", "")
        post = post_map.pop(pid, None)
        if post is None:
            continue
        try:
            score = max(0.0, min(1.0, float(item.get("score", 0.5))))
        except (ValueError, TypeError):
            score = 0.5
        scored.append(ScoredPost(post=post, score=score, explanation=item.get("explanation", "")))

    for post in post_map.values():
        scored.append(ScoredPost(post=post, score=0.5, explanation="Not scored by AI"))

    return scored


def _build_filters_text(ai_config: AIConfig) -> str:
    """Build the {filters} section of the prompt from config toggles."""
    lines = []
    if ai_config.prefer_questions:
        lines.append("- BOOST posts that are questions or asking for help (score higher).")
    if ai_config.prefer_unanswered:
        lines.append("- BOOST posts that appear unanswered or have 0 replies (early engagement opportunity).")
    if ai_config.exclude_self_promo:
        lines.append("- PENALIZE posts that are self-promotion, spam, or product announcements (score lower).")
    if lines:
        return "Additional scoring rules:\n" + "\n".join(lines)
    return ""


class Scorer:
    """AI-powered relevance scoring with multi-provider support."""

    def __init__(self, ai_config: AIConfig):
        self.ai_config = ai_config
        self.provider = ai_config.provider
        self.api_key = ai_config.api_key
        self.model = ai_config.model or self._default_model()

    def _default_model(self) -> str:
        if self.provider == "claude":
            return "claude-haiku-4-5-20251001"
        elif self.provider == "openrouter":
            return "anthropic/claude-haiku-4-5-20251001"
        return "gpt-4o-mini"

    def status_text(self) -> str:
        return f"AI: {self.provider.title()} / {self.model}"

    async def score_batch(
        self,
        posts: list[Post],
        keywords: list[str],
        interests: str,
    ) -> list[ScoredPost]:
        if not posts:
            return []

        # Build system prompt from config or default
        prompt_template = self.ai_config.prompt.strip() or DEFAULT_AI_PROMPT
        filters_text = _build_filters_text(self.ai_config)

        system = prompt_template.format(
            interests=interests or "Not specified",
            keywords=", ".join(keywords) if keywords else "None specified",
            filters=filters_text,
        )

        user_msg = USER_PROMPT_TEMPLATE.format(
            count=len(posts),
            posts_text=_format_posts_for_scoring(posts),
        )

        try:
            if self.provider == "claude":
                response_text = await self._call_claude(system, user_msg)
            elif self.provider == "openrouter":
                response_text = await self._call_openrouter(system, user_msg)
            else:
                response_text = await self._call_openai(system, user_msg)

            return _parse_scores(response_text, posts)

        except Exception as e:
            error_msg = str(e)
            logger.exception("AI scoring failed — returning default scores")
            # Include the actual error in the explanation so user can see it
            short_err = error_msg[:150] if len(error_msg) > 150 else error_msg
            return [ScoredPost(post=p, score=0.5, explanation=f"AI error: {short_err}") for p in posts]

    async def _call_claude(self, system: str, user_msg: str) -> str:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=self.api_key)
        response = await client.messages.create(
            model=self.model, max_tokens=2048, system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text

    async def _call_openai(self, system: str, user_msg: str) -> str:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=self.api_key)
        response = await client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            max_tokens=2048, temperature=0.1,
        )
        return response.choices[0].message.content or ""

    async def _call_openrouter(self, system: str, user_msg: str) -> str:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=self.api_key, base_url="https://openrouter.ai/api/v1")
        response = await client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            max_tokens=2048, temperature=0.1,
        )
        return response.choices[0].message.content or ""
