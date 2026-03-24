"""Content pipeline demo agents.

Simulated content moderation agents using keyword matching and pattern detection.
Each agent sleeps briefly to simulate processing time.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any


async def classify_content(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    """Classify content type by keyword matching."""
    await asyncio.sleep(0.3)

    text = input.get("work_item_state", {}).get("text", "").lower()

    if any(w in text for w in ("article", "report", "study", "research", "analysis")):
        content_type = "article"
    elif any(w in text for w in ("caption", "image", "photo", "picture")):
        content_type = "image_caption"
    else:
        content_type = "comment"

    return {
        "output": {"type": content_type, "language": "en"},
        "escalate": False,
    }


async def analyze_sentiment(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    """Analyze sentiment of content text."""
    await asyncio.sleep(0.4)

    text = input.get("work_item_state", {}).get("text", "").lower()

    positive_words = {"good", "great", "excellent", "love", "wonderful", "happy", "best", "amazing", "helpful", "thank"}
    negative_words = {"bad", "terrible", "awful", "worst", "hate", "angry", "horrible", "disgusting", "pathetic"}

    words = set(re.findall(r"\w+", text))
    pos_count = len(words & positive_words)
    neg_count = len(words & negative_words)
    total = pos_count + neg_count

    if total == 0:
        sentiment = "neutral"
        score = 0.5
    elif pos_count > neg_count:
        sentiment = "positive"
        score = min(1.0, 0.5 + pos_count / (total * 2))
    else:
        sentiment = "negative"
        score = max(0.0, 0.5 - neg_count / (total * 2))

    return {
        "output": {"sentiment": sentiment, "sentiment_score": score},
        "escalate": False,
    }


async def score_toxicity(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    """Score content toxicity by keyword list matching."""
    await asyncio.sleep(0.35)

    text = input.get("work_item_state", {}).get("text", "").lower()
    toxic_words = {"hate", "kill", "stupid", "idiot", "die", "attack", "destroy", "moron", "loser", "shut up", "dumb"}

    words = set(re.findall(r"\w+", text))
    flagged = sorted(words & toxic_words)
    score = min(1.0, len(flagged) * 0.25)

    return {
        "output": {"toxicity_score": score, "flagged_words": flagged},
        "escalate": False,
    }


async def detect_spam(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    """Detect spam patterns in content text."""
    await asyncio.sleep(0.4)

    text = input.get("work_item_state", {}).get("text", "")
    indicators = []

    # ALL CAPS check
    alpha_chars = [c for c in text if c.isalpha()]
    if alpha_chars and sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars) > 0.6:
        indicators.append("excessive_caps")

    # Repeated characters (e.g., "!!!!!!")
    if re.search(r"(.)\1{4,}", text):
        indicators.append("repeated_chars")

    # URL density
    urls = re.findall(r"https?://\S+", text)
    if len(urls) >= 2:
        indicators.append("multiple_urls")

    # Exclamation density
    if text.count("!") >= 3:
        indicators.append("excessive_exclamation")

    score = min(1.0, len(indicators) * 0.25)

    return {
        "output": {"spam_score": score, "indicators": indicators},
        "escalate": False,
    }
