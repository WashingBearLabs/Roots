"""Research Assistant Demo — mock research agents.

Each agent returns plausible results based on the topic in the work item.
Topics are used to seed different result sets for reproducibility.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

# Canned result pools keyed by topic hash bucket
_ACADEMIC_PAPERS = [
    {"title": "A Survey of Recent Advances in the Field", "abstract": "This paper reviews the latest developments and emerging trends.", "year": 2025, "journal": "Nature Reviews"},
    {"title": "Foundations and Future Directions", "abstract": "We present a comprehensive analysis of foundational principles and outline promising research avenues.", "year": 2024, "journal": "Science"},
    {"title": "Empirical Analysis and Benchmarking", "abstract": "Large-scale empirical study comparing leading approaches across standardized benchmarks.", "year": 2025, "journal": "PNAS"},
    {"title": "Ethical Considerations and Societal Impact", "abstract": "Examining the broader implications for society, policy, and governance.", "year": 2024, "journal": "Nature Human Behaviour"},
    {"title": "Cross-disciplinary Applications and Innovations", "abstract": "Novel applications bridging multiple domains with promising real-world results.", "year": 2025, "journal": "IEEE Transactions"},
]

_NEWS_ARTICLES = [
    {"headline": "Major Breakthrough Announced by Leading Research Lab", "source": "Reuters", "date": "2026-03-15"},
    {"headline": "Industry Leaders Gather to Discuss Future Implications", "source": "BBC News", "date": "2026-03-10"},
    {"headline": "Government Announces New Funding Initiative", "source": "The Guardian", "date": "2026-03-08"},
    {"headline": "Startup Raises $50M to Commercialize New Technology", "source": "TechCrunch", "date": "2026-03-05"},
    {"headline": "Experts Warn of Potential Risks and Challenges", "source": "New York Times", "date": "2026-02-28"},
]

_WEB_PAGES = [
    {"title": "Comprehensive Guide and Getting Started Tutorial", "url": "https://example.com/guide", "snippet": "Everything you need to know to get started, from basics to advanced topics."},
    {"title": "Community Discussion: Best Practices and Tips", "url": "https://example.com/forum/best-practices", "snippet": "Community-curated collection of best practices gathered from real-world experience."},
    {"title": "Open Source Tools and Resources Directory", "url": "https://example.com/tools", "snippet": "Curated list of open-source tools, libraries, and frameworks available for use."},
    {"title": "Comparison: Top 5 Approaches Reviewed", "url": "https://example.com/comparison", "snippet": "Side-by-side comparison of the leading approaches with pros and cons."},
    {"title": "Case Study: Real-World Implementation Success Story", "url": "https://example.com/case-study", "snippet": "Detailed walkthrough of a successful real-world implementation."},
]


def _topic_hash(topic: str) -> int:
    """Deterministic hash of topic string for result selection."""
    return int(hashlib.md5(topic.encode()).hexdigest(), 16)


def _select_results(pool: list[dict[str, Any]], topic: str, count: int = 4) -> list[dict[str, Any]]:
    """Select deterministic subset of results based on topic."""
    h = _topic_hash(topic)
    start = h % len(pool)
    selected = []
    for i in range(count):
        idx = (start + i) % len(pool)
        item = dict(pool[idx])
        # Inject topic into titles/headlines for realism
        if "title" in item and i == 0:
            item["title"] = f"{item['title']}: {topic}"
        if "headline" in item and i == 0:
            item["headline"] = f"{topic} — {item['headline']}"
        selected.append(item)
    return selected


async def search_academic(input: dict[str, Any]) -> dict[str, Any]:
    """Search academic papers. Returns papers related to the topic."""
    await asyncio.sleep(0.4)
    topic = input.get("work_item_state", {}).get("topic", "general research")
    papers = _select_results(_ACADEMIC_PAPERS, topic, count=4)
    return {"papers": papers, "source": "academic", "query": topic}


async def search_news(input: dict[str, Any]) -> dict[str, Any]:
    """Search news articles. Returns recent news related to the topic."""
    await asyncio.sleep(0.5)
    topic = input.get("work_item_state", {}).get("topic", "general research")
    articles = _select_results(_NEWS_ARTICLES, topic, count=3)
    return {"articles": articles, "source": "news", "query": topic}


async def search_web(input: dict[str, Any]) -> dict[str, Any]:
    """Search web pages. Returns web resources related to the topic."""
    await asyncio.sleep(0.3)
    topic = input.get("work_item_state", {}).get("topic", "general research")
    pages = _select_results(_WEB_PAGES, topic, count=4)
    return {"pages": pages, "source": "web", "query": topic}


async def summarize_results(input: dict[str, Any]) -> dict[str, Any]:
    """Combine research results into a summary."""
    await asyncio.sleep(0.3)
    state = input.get("work_item_state", {})
    topic = state.get("topic", "the topic")
    research = state.get("research_results", [])

    # Count total sources across all branches
    source_count = 0
    key_findings = []
    for branch in research:
        branch_state = branch.get("state", {})
        for key, val in branch_state.items():
            if isinstance(val, dict):
                items = val.get("papers", val.get("articles", val.get("pages", [])))
                if isinstance(items, list):
                    source_count += len(items)
                    if items:
                        first = items[0]
                        title = first.get("title", first.get("headline", ""))
                        if title:
                            key_findings.append(title)

    summary_text = (
        f"Research on '{topic}' gathered {source_count} sources across academic papers, "
        f"news articles, and web resources. Key findings span recent breakthroughs, "
        f"industry developments, and community best practices."
    )

    return {
        "summary_text": summary_text,
        "source_count": source_count,
        "key_findings": key_findings[:5],
        "topic": topic,
    }
