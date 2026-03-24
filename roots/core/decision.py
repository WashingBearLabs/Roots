"""Decision engine for deterministic and AI-based decision evaluation."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import litellm

logger = logging.getLogger(__name__)
from pydantic import BaseModel, Field

from roots.core.schema import DecisionEdge, DecisionMode, DecisionNodeConfig, NodeDefinition

from simpleeval import (  # type: ignore[import-untyped]
    AttributeDoesNotExist,
    EvalWithCompoundTypes,
    NameNotDefined,
)


class DecisionEvaluationError(Exception):
    """Raised when a decision expression cannot be evaluated."""

    def __init__(
        self,
        expression: str,
        message: str,
        *,
        node_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.expression = expression
        self.node_id = node_id
        self.context = context
        if node_id:
            super().__init__(
                f"Node '{node_id}': failed to evaluate '{expression}': {message}"
            )
        else:
            super().__init__(f"Failed to evaluate '{expression}': {message}")


def flatten_for_eval(state: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict for simpleeval name resolution.

    Converts {"output": {"severity": "critical"}} into:
        {"output.severity": "critical", "output": {"severity": "critical"}}

    Also walks lists so {"results": [{"name": "a"}]} becomes:
        {"results.0.name": "a", "results.0": {"name": "a"}, ...}
    """
    result: dict[str, Any] = {}
    for key, value in state.items():
        full_key = f"{prefix}{key}" if prefix else key
        if isinstance(value, dict):
            result[full_key] = value
            result.update(flatten_for_eval(value, prefix=f"{full_key}."))
        elif isinstance(value, list):
            result[full_key] = value
            for i, item in enumerate(value):
                idx_key = f"{full_key}.{i}"
                if isinstance(item, dict):
                    result[idx_key] = item
                    result.update(flatten_for_eval(item, prefix=f"{idx_key}."))
                else:
                    result[idx_key] = item
        else:
            result[full_key] = value
    return result


_ARRAY_DOT_RE = re.compile(r"(?<=[A-Za-z_\]])\.(\d+)\b")


def evaluate_condition(expression: str, state: dict[str, Any]) -> bool:
    """Evaluate a condition expression against work item state.

    Uses simpleeval for safe evaluation — no builtins or function calls.
    Dot-notation array indices (e.g. ``results.0.name``) are converted to
    bracket notation (``results[0].name``) before evaluation.

    Raises:
        DecisionEvaluationError: If the expression cannot be evaluated.
    """
    names = flatten_for_eval(state)
    parsed_expr = _ARRAY_DOT_RE.sub(r"[\1]", expression)
    evaluator = EvalWithCompoundTypes(names=names, functions={})
    evaluator.MAX_STRING_LENGTH = 100_000  # type: ignore[misc]
    evaluator.MAX_COMPREHENSION_LENGTH = 1000  # type: ignore[misc]
    evaluator.MAX_POWER = 100  # type: ignore[misc]
    try:
        result = evaluator.eval(parsed_expr)
    except NameNotDefined as exc:
        raise DecisionEvaluationError(
            expression, f"unknown field: {exc.name}"
        ) from exc
    except AttributeDoesNotExist as exc:
        raise DecisionEvaluationError(
            expression, f"unknown field: {exc.attr}"  # type: ignore[attr-defined]
        ) from exc
    except TypeError as exc:
        raise DecisionEvaluationError(
            expression, f"type error: {exc}"
        ) from exc
    except Exception as exc:
        raise DecisionEvaluationError(
            expression, str(exc)
        ) from exc
    return bool(result)


class AIDecisionResponse(BaseModel):
    """Structured response model for AI decision modes."""

    selected_edge_target: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class DecisionResult(BaseModel):
    """Result of a decision evaluation."""

    selected_edge: str
    mode: str
    confidence: float
    reasoning: str | None = None
    escalated: bool = False
    ai_recommendation: AIDecisionResponse | None = None
    checkpoint_prompt: str | None = None

    def to_decision_record(self, input_state: dict[str, Any]) -> dict[str, Any]:
        """Produce a complete record dict for decision history storage.

        Args:
            input_state: The work item state snapshot at the time of decision.

        Returns:
            Dict with mode, selected_edge, confidence, reasoning,
            escalated, and input_state_snapshot.
        """
        record: dict[str, Any] = {
            "mode": self.mode,
            "selected_edge": self.selected_edge,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "escalated": self.escalated,
            "input_state_snapshot": input_state,
        }
        if self.checkpoint_prompt is not None:
            record["checkpoint_prompt"] = self.checkpoint_prompt
        if self.ai_recommendation is not None:
            record["ai_recommendation"] = self.ai_recommendation.model_dump()
        return record


DECISION_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "make_decision",
        "description": "Select the next step in the process",
        "parameters": {
            "type": "object",
            "properties": {
                "selected_edge_target": {
                    "type": "string",
                    "description": "The target node ID to route to",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
                "reasoning": {"type": "string"},
            },
            "required": ["selected_edge_target", "confidence", "reasoning"],
        },
    },
}

SYSTEM_PROMPT = (
    "You are a decision evaluator in a process orchestration system. "
    "Evaluate the current state and select the most appropriate next step."
)

_MARKDOWN_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


def build_decision_messages(
    context_prompt: str | None,
    state: dict[str, Any],
    edges: list[DecisionEdge],
) -> list[dict[str, str]]:
    """Build the message list for an AI decision LLM call."""
    edge_descriptions = []
    for edge in edges:
        entry = f"- target: {edge.target}"
        if edge.label:
            entry += f", label: {edge.label}"
        if edge.description:
            entry += f", description: {edge.description}"
        edge_descriptions.append(entry)

    user_parts: list[str] = []
    if context_prompt:
        user_parts.append(f"## Context\n{context_prompt}")
    user_parts.append(f"## Current State\n```json\n{json.dumps(state, indent=2)}\n```")
    user_parts.append(f"## Available Edges\n" + "\n".join(edge_descriptions))
    user_parts.append(
        "Select the most appropriate next step by calling the make_decision tool."
    )

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def resolve_model(node_config: DecisionNodeConfig, default_model: str) -> str:
    """Resolve the model to use: node config → default_model."""
    return node_config.model or default_model


def parse_ai_response(response: Any) -> AIDecisionResponse:
    """Parse an LLM response into AIDecisionResponse.

    Tries tool_calls first, then falls back to parsing text content as JSON.

    Raises:
        DecisionEvaluationError: If neither path produces a valid response.
    """
    message = response.choices[0].message

    # Primary path: tool call
    if message.tool_calls:
        try:
            args = json.loads(message.tool_calls[0].function.arguments)
            return AIDecisionResponse.model_validate(args)
        except (json.JSONDecodeError, Exception) as exc:
            # Fall through to text path
            pass

    # Fallback path: text content
    content = message.content
    if content:
        # Strip markdown fences if present
        fence_match = _MARKDOWN_FENCE_RE.search(content)
        if fence_match:
            content = fence_match.group(1).strip()
        try:
            data = json.loads(content)
            return AIDecisionResponse.model_validate(data)
        except (json.JSONDecodeError, Exception):
            pass

    # Both failed
    raw = getattr(message, "content", None) or str(message)
    raise DecisionEvaluationError(
        expression="AI response parsing",
        message=f"Could not parse AI response as AIDecisionResponse. Raw: {raw}",
    )


class DecisionEngine:
    """Evaluates decision nodes to determine execution routing."""

    def __init__(self, default_model: str) -> None:
        self._default_model = default_model

    async def evaluate(
        self, node: NodeDefinition, work_item_state: dict[str, Any]
    ) -> DecisionResult:
        """Evaluate a decision node against work item state.

        Args:
            node: The decision node to evaluate.
            work_item_state: Current work item state dict.

        Returns:
            DecisionResult with the selected edge target.

        Raises:
            DecisionEvaluationError: If no condition matches or evaluation fails.
        """
        assert isinstance(node.config, DecisionNodeConfig)

        if node.config.mode == DecisionMode.DETERMINISTIC:
            return self._evaluate_deterministic(node, work_item_state)

        if node.config.mode in (DecisionMode.AI_BOUNDED, DecisionMode.AI_AUTONOMOUS):
            return await self._evaluate_ai(node, work_item_state)

        if node.config.mode == DecisionMode.AI_CHECKPOINT:
            return await self._evaluate_ai_checkpoint(node, work_item_state)

        raise NotImplementedError(
            f"Decision mode '{node.config.mode}' is not yet implemented"
        )

    def _evaluate_deterministic(
        self,
        node: NodeDefinition,
        work_item_state: dict[str, Any],
    ) -> DecisionResult:
        """Evaluate a deterministic decision node (first matching edge wins)."""
        assert isinstance(node.config, DecisionNodeConfig)

        conditions_tried: list[str] = []
        for edge in node.config.edges:
            assert edge.condition is not None
            conditions_tried.append(edge.condition)
            if evaluate_condition(edge.condition, work_item_state):
                return DecisionResult(
                    selected_edge=edge.target,
                    mode=DecisionMode.DETERMINISTIC,
                    confidence=1.0,
                    reasoning=f"Matched condition: {edge.condition}",
                )

        raise DecisionEvaluationError(
            expression=", ".join(conditions_tried),
            message=(
                f"no condition matched. "
                f"Conditions tried: {conditions_tried}. "
                f"State: {work_item_state}"
            ),
            node_id=node.id,
            context=work_item_state,
        )

    async def _call_ai_decision(
        self, node: NodeDefinition, work_item_state: dict[str, Any]
    ) -> AIDecisionResponse:
        """Call LiteLLM and parse the AI response.

        Returns the parsed AIDecisionResponse.  Edge-target validation
        is performed by the caller so that invalid targets can be
        escalated instead of crashing.
        """
        assert isinstance(node.config, DecisionNodeConfig)
        model = resolve_model(node.config, self._default_model)
        messages = build_decision_messages(
            node.config.context_prompt,
            work_item_state,
            node.config.edges,
        )
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            tools=[DECISION_TOOL],
            tool_choice={"type": "function", "function": {"name": "make_decision"}},
        )
        return parse_ai_response(response)

    async def _evaluate_ai(
        self,
        node: NodeDefinition,
        work_item_state: dict[str, Any],
    ) -> DecisionResult:
        """Evaluate an AI decision node (ai_bounded or ai_autonomous)."""
        assert isinstance(node.config, DecisionNodeConfig)
        ai_response = await self._call_ai_decision(node, work_item_state)

        # Validate edge target — escalate instead of crashing
        valid_targets = {edge.target for edge in node.config.edges}
        if ai_response.selected_edge_target not in valid_targets:
            logger.warning(
                "AI selected invalid edge target '%s' for node '%s'. Escalating to checkpoint.",
                ai_response.selected_edge_target,
                node.id,
            )
            return DecisionResult(
                selected_edge=ai_response.selected_edge_target,
                mode=node.config.mode.value,
                confidence=ai_response.confidence,
                reasoning=ai_response.reasoning,
                escalated=True,
                ai_recommendation=ai_response,
                checkpoint_prompt=getattr(node.config, 'checkpoint_prompt', None),
            )

        assert node.config.confidence_threshold is not None
        if ai_response.confidence < node.config.confidence_threshold:
            return DecisionResult(
                selected_edge=ai_response.selected_edge_target,
                mode=node.config.mode,
                confidence=ai_response.confidence,
                reasoning=ai_response.reasoning,
                escalated=True,
                ai_recommendation=ai_response,
            )
        return DecisionResult(
            selected_edge=ai_response.selected_edge_target,
            mode=node.config.mode,
            confidence=ai_response.confidence,
            reasoning=ai_response.reasoning,
            ai_recommendation=ai_response,
        )

    async def _evaluate_ai_checkpoint(
        self,
        node: NodeDefinition,
        work_item_state: dict[str, Any],
    ) -> DecisionResult:
        """Evaluate an AI checkpoint decision node.

        Same as AI bounded/autonomous but always escalates for human confirmation.
        """
        assert isinstance(node.config, DecisionNodeConfig)
        ai_response = await self._call_ai_decision(node, work_item_state)

        # Validate edge target — escalate instead of crashing
        valid_targets = {edge.target for edge in node.config.edges}
        if ai_response.selected_edge_target not in valid_targets:
            logger.warning(
                "AI selected invalid edge target '%s' for node '%s'. Escalating to checkpoint.",
                ai_response.selected_edge_target,
                node.id,
            )
            return DecisionResult(
                selected_edge=ai_response.selected_edge_target,
                mode=node.config.mode.value,
                confidence=ai_response.confidence,
                reasoning=ai_response.reasoning,
                escalated=True,
                ai_recommendation=ai_response,
                checkpoint_prompt=getattr(node.config, 'checkpoint_prompt', None),
            )

        return DecisionResult(
            selected_edge=ai_response.selected_edge_target,
            mode=node.config.mode,
            confidence=ai_response.confidence,
            reasoning=ai_response.reasoning,
            escalated=True,
            ai_recommendation=ai_response,
            checkpoint_prompt=node.config.checkpoint_prompt,
        )
