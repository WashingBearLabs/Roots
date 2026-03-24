"""Minimal LLM client using the OpenAI-compatible chat completions API.

Replaces litellm dependency. Supports any provider that implements the
OpenAI chat completions format (OpenAI, Ollama, Together, Groq, vLLM,
Anthropic via OpenAI-compatible proxy, etc.).

For providers with non-standard APIs, pass a custom callable to the
DecisionEngine instead.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)


class LLMCompletionFunc(Protocol):
    """Protocol for LLM completion callables."""
    async def __call__(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | str | None = None,
    ) -> LLMResponse: ...


@dataclass
class ToolCall:
    """Represents a tool call from the LLM response."""
    name: str
    arguments: str  # JSON string


@dataclass
class LLMResponse:
    """Normalized response from an LLM completion call."""
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMConfig:
    """Configuration for the built-in OpenAI-compatible client."""
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = None
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        # Auto-detect from environment if not provided
        if self.api_key is None:
            self.api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("ROOTS_LLM_API_KEY")
        if base_url_env := os.environ.get("ROOTS_LLM_BASE_URL"):
            self.base_url = base_url_env


async def openai_chat_completion(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: dict[str, Any] | str | None = None,
    *,
    config: LLMConfig | None = None,
) -> LLMResponse:
    """Make a chat completion call to an OpenAI-compatible API.

    Works with: OpenAI, Ollama (/v1), Together, Groq, vLLM, LM Studio,
    Anthropic (via OpenAI-compatible endpoint), and any other provider
    that implements the OpenAI chat completions format.
    """
    if config is None:
        config = LLMConfig()

    url = f"{config.base_url.rstrip('/')}/chat/completions"

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice

    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # Parse response
    choice = data.get("choices", [{}])[0]
    message = choice.get("message", {})

    content = message.get("content")

    tool_calls_data = message.get("tool_calls", [])
    tool_calls = []
    for tc in tool_calls_data:
        fn = tc.get("function", {})
        tool_calls.append(ToolCall(
            name=fn.get("name", ""),
            arguments=fn.get("arguments", "{}"),
        ))

    return LLMResponse(content=content, tool_calls=tool_calls, raw=data)
