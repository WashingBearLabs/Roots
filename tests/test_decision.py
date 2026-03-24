"""Tests for decision engine (US-001, US-002, US-003, US-004, US-005)."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from roots.core.decision import (
    AIDecisionResponse,
    DECISION_TOOL,
    DecisionEngine,
    DecisionEvaluationError,
    DecisionResult,
    build_decision_messages,
    evaluate_condition,
    flatten_for_eval,
    parse_ai_response,
    resolve_model,
)
from roots.core.schema import (
    DecisionEdge,
    DecisionMode,
    DecisionNodeConfig,
    NodeDefinition,
    NodeType,
)


# --- flatten_for_eval ---


class TestFlattenForEval:
    def test_simple_flat_dict(self) -> None:
        state = {"status": "done", "count": 5}
        result = flatten_for_eval(state)
        assert result["status"] == "done"
        assert result["count"] == 5

    def test_nested_dict(self) -> None:
        state = {"output": {"severity": "critical"}}
        result = flatten_for_eval(state)
        assert result["output.severity"] == "critical"
        assert result["output"] == {"severity": "critical"}

    def test_deeply_nested(self) -> None:
        state = {"a": {"b": {"c": "deep"}}}
        result = flatten_for_eval(state)
        assert result["a.b.c"] == "deep"
        assert result["a.b"] == {"c": "deep"}
        assert result["a"] == {"b": {"c": "deep"}}

    def test_array_indexing(self) -> None:
        state = {"results": [{"name": "a"}, {"name": "b"}]}
        result = flatten_for_eval(state)
        assert result["results.0.name"] == "a"
        assert result["results.1.name"] == "b"
        assert result["results.0"] == {"name": "a"}
        assert result["results"] == [{"name": "a"}, {"name": "b"}]

    def test_array_with_scalars(self) -> None:
        state = {"tags": ["alpha", "beta"]}
        result = flatten_for_eval(state)
        assert result["tags.0"] == "alpha"
        assert result["tags.1"] == "beta"
        assert result["tags"] == ["alpha", "beta"]


# --- evaluate_condition: dot notation ---


class TestDotNotation:
    def test_nested_field_access(self) -> None:
        state = {"output": {"severity": "critical"}}
        assert evaluate_condition("output.severity == 'critical'", state) is True

    def test_deeply_nested_access(self) -> None:
        state = {"a": {"b": {"c": 42}}}
        assert evaluate_condition("a.b.c == 42", state) is True

    def test_top_level_access(self) -> None:
        state = {"status": "done"}
        assert evaluate_condition("status == 'done'", state) is True


# --- evaluate_condition: comparison operators ---


class TestComparisonOperators:
    def test_equal_string(self) -> None:
        assert evaluate_condition("x == 'hello'", {"x": "hello"}) is True

    def test_not_equal(self) -> None:
        assert evaluate_condition("x != 'hello'", {"x": "world"}) is True

    def test_greater_than(self) -> None:
        assert evaluate_condition("x > 5", {"x": 10}) is True

    def test_less_than(self) -> None:
        assert evaluate_condition("x < 5", {"x": 3}) is True

    def test_greater_equal(self) -> None:
        assert evaluate_condition("x >= 5", {"x": 5}) is True

    def test_less_equal(self) -> None:
        assert evaluate_condition("x <= 5", {"x": 5}) is True

    def test_number_comparison(self) -> None:
        assert evaluate_condition("score > 0.5", {"score": 0.8}) is True
        assert evaluate_condition("score > 0.5", {"score": 0.3}) is False


# --- evaluate_condition: in operator ---


class TestInOperator:
    def test_in_list(self) -> None:
        state = {"status": "error", "failure_modes": ["error", "timeout"]}
        assert evaluate_condition("status in failure_modes", state) is True

    def test_not_in_list(self) -> None:
        state = {"status": "ok", "failure_modes": ["error", "timeout"]}
        assert evaluate_condition("status in failure_modes", state) is False

    def test_in_literal_list(self) -> None:
        assert evaluate_condition("x in ['a', 'b', 'c']", {"x": "b"}) is True
        assert evaluate_condition("x in ['a', 'b', 'c']", {"x": "z"}) is False


# --- evaluate_condition: boolean operators ---


class TestBooleanOperators:
    def test_and(self) -> None:
        state = {"a": True, "b": True}
        assert evaluate_condition("a and b", state) is True
        state["b"] = False
        assert evaluate_condition("a and b", state) is False

    def test_or(self) -> None:
        state = {"a": False, "b": True}
        assert evaluate_condition("a or b", state) is True

    def test_not(self) -> None:
        assert evaluate_condition("not x", {"x": False}) is True
        assert evaluate_condition("not x", {"x": True}) is False

    def test_complex_boolean(self) -> None:
        state = {"output": {"severity": "critical"}, "retry_count": 3}
        expr = "output.severity == 'critical' and retry_count > 2"
        assert evaluate_condition(expr, state) is True

    def test_combined_not_and(self) -> None:
        state = {"a": True, "b": False}
        assert evaluate_condition("a and not b", state) is True


# --- evaluate_condition: missing keys ---


class TestMissingKeys:
    def test_missing_field_raises_error(self) -> None:
        with pytest.raises(DecisionEvaluationError, match="unknown field"):
            evaluate_condition("nonexistent == 'x'", {"other": 1})

    def test_error_contains_expression(self) -> None:
        expr = "missing_field > 5"
        with pytest.raises(DecisionEvaluationError) as exc_info:
            evaluate_condition(expr, {})
        assert exc_info.value.expression == expr

    def test_missing_nested_field(self) -> None:
        with pytest.raises(DecisionEvaluationError, match="unknown field"):
            evaluate_condition("output.missing == 'x'", {"output": {"a": 1}})


# --- evaluate_condition: type mismatches ---


class TestTypeMismatches:
    def test_compare_string_to_int(self) -> None:
        with pytest.raises(DecisionEvaluationError, match="type error"):
            evaluate_condition("x > 5", {"x": "not_a_number"})


# --- evaluate_condition: security ---


class TestSecurity:
    def test_no_builtins_access(self) -> None:
        with pytest.raises(DecisionEvaluationError):
            evaluate_condition("__import__('os').system('echo hi')", {})

    def test_no_function_calls(self) -> None:
        with pytest.raises(DecisionEvaluationError):
            evaluate_condition("len([1, 2, 3])", {})


# --- evaluate_condition: array indexing ---


class TestArrayIndexing:
    def test_array_element_field(self) -> None:
        state = {"results": [{"name": "a"}, {"name": "b"}]}
        assert evaluate_condition("results.0.name == 'a'", state) is True
        assert evaluate_condition("results.1.name == 'b'", state) is True

    def test_array_element_comparison(self) -> None:
        state = {"scores": [10, 20, 30]}
        assert evaluate_condition("scores.0 == 10", state) is True
        assert evaluate_condition("scores.2 > 25", state) is True


# --- evaluate_condition: literals ---


class TestLiterals:
    def test_none_comparison(self) -> None:
        assert evaluate_condition("x == None", {"x": None}) is True
        assert evaluate_condition("x != None", {"x": "val"}) is True

    def test_bool_literal(self) -> None:
        assert evaluate_condition("x == True", {"x": True}) is True
        assert evaluate_condition("x == False", {"x": False}) is True


# --- DecisionEngine: deterministic mode (US-002) ---


def _make_decision_node(
    edges: list[DecisionEdge],
    node_id: str = "decide-1",
) -> NodeDefinition:
    """Helper to create a deterministic decision node."""
    return NodeDefinition(
        id=node_id,
        type=NodeType.DECISION,
        label="Test Decision",
        config=DecisionNodeConfig(
            mode=DecisionMode.DETERMINISTIC,
            edges=edges,
        ),
    )


class TestDecisionEngineDeterministic:
    @pytest.fixture
    def engine(self) -> DecisionEngine:
        return DecisionEngine(default_model="test-model")

    @pytest.mark.asyncio
    async def test_single_match(self, engine: DecisionEngine) -> None:
        node = _make_decision_node([
            DecisionEdge(target="node-a", condition="status == 'done'"),
        ])
        result = await engine.evaluate(node, {"status": "done"})
        assert result.selected_edge == "node-a"
        assert result.mode == "deterministic"
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_first_of_multiple_match(self, engine: DecisionEngine) -> None:
        node = _make_decision_node([
            DecisionEdge(target="node-a", condition="x > 10"),
            DecisionEdge(target="node-b", condition="x > 5"),
            DecisionEdge(target="node-c", condition="x > 0"),
        ])
        result = await engine.evaluate(node, {"x": 8})
        assert result.selected_edge == "node-b"

    @pytest.mark.asyncio
    async def test_edge_order_matters(self, engine: DecisionEngine) -> None:
        """Both conditions match, but first edge wins."""
        node = _make_decision_node([
            DecisionEdge(target="node-first", condition="x > 0"),
            DecisionEdge(target="node-second", condition="x > 0"),
        ])
        result = await engine.evaluate(node, {"x": 5})
        assert result.selected_edge == "node-first"

    @pytest.mark.asyncio
    async def test_no_match_raises_error(self, engine: DecisionEngine) -> None:
        node = _make_decision_node(
            [
                DecisionEdge(target="node-a", condition="status == 'done'"),
                DecisionEdge(target="node-b", condition="status == 'error'"),
            ],
            node_id="decide-routing",
        )
        with pytest.raises(DecisionEvaluationError) as exc_info:
            await engine.evaluate(node, {"status": "pending"})
        err = exc_info.value
        assert err.node_id == "decide-routing"
        assert err.context == {"status": "pending"}
        assert "no condition matched" in str(err)
        assert "status == 'done'" in str(err)
        assert "status == 'error'" in str(err)

    @pytest.mark.asyncio
    async def test_confidence_always_one(self, engine: DecisionEngine) -> None:
        node = _make_decision_node([
            DecisionEdge(target="node-a", condition="x == 1"),
        ])
        result = await engine.evaluate(node, {"x": 1})
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_result_includes_reasoning(self, engine: DecisionEngine) -> None:
        node = _make_decision_node([
            DecisionEdge(target="node-a", condition="x == 1"),
        ])
        result = await engine.evaluate(node, {"x": 1})
        assert result.reasoning is not None
        assert "x == 1" in result.reasoning


# --- AIDecisionResponse model (US-003) ---


class TestAIDecisionResponse:
    def test_valid_response(self) -> None:
        resp = AIDecisionResponse(
            selected_edge_target="node-a",
            confidence=0.85,
            reasoning="High severity warrants escalation",
        )
        assert resp.selected_edge_target == "node-a"
        assert resp.confidence == 0.85
        assert resp.reasoning == "High severity warrants escalation"

    def test_confidence_lower_bound(self) -> None:
        resp = AIDecisionResponse(
            selected_edge_target="node-a", confidence=0.0, reasoning="Low"
        )
        assert resp.confidence == 0.0

    def test_confidence_upper_bound(self) -> None:
        resp = AIDecisionResponse(
            selected_edge_target="node-a", confidence=1.0, reasoning="High"
        )
        assert resp.confidence == 1.0

    def test_confidence_below_zero_rejected(self) -> None:
        with pytest.raises(Exception):
            AIDecisionResponse(
                selected_edge_target="node-a", confidence=-0.1, reasoning="Bad"
            )

    def test_confidence_above_one_rejected(self) -> None:
        with pytest.raises(Exception):
            AIDecisionResponse(
                selected_edge_target="node-a", confidence=1.1, reasoning="Bad"
            )

    def test_model_validate_from_dict(self) -> None:
        data = {
            "selected_edge_target": "node-b",
            "confidence": 0.5,
            "reasoning": "Moderate confidence",
        }
        resp = AIDecisionResponse.model_validate(data)
        assert resp.selected_edge_target == "node-b"


# --- Prompt template (US-003) ---


class TestBuildDecisionMessages:
    def test_includes_system_prompt(self) -> None:
        messages = build_decision_messages("Check severity", {}, [
            DecisionEdge(target="node-a"),
        ])
        assert messages[0]["role"] == "system"
        assert "decision evaluator" in messages[0]["content"]

    def test_includes_context_prompt(self) -> None:
        messages = build_decision_messages("Check severity", {"x": 1}, [
            DecisionEdge(target="node-a"),
        ])
        assert "Check severity" in messages[1]["content"]

    def test_includes_state_json(self) -> None:
        state = {"output": {"severity": "critical"}}
        messages = build_decision_messages(None, state, [
            DecisionEdge(target="node-a"),
        ])
        content = messages[1]["content"]
        assert '"severity": "critical"' in content

    def test_includes_edge_descriptions(self) -> None:
        edges = [
            DecisionEdge(
                target="escalate",
                label="Escalate",
                description="Route to human review",
            ),
            DecisionEdge(target="auto-resolve", label="Auto"),
        ]
        messages = build_decision_messages("Pick", {}, edges)
        content = messages[1]["content"]
        assert "escalate" in content
        assert "Escalate" in content
        assert "Route to human review" in content
        assert "auto-resolve" in content

    def test_no_context_prompt(self) -> None:
        messages = build_decision_messages(None, {"x": 1}, [
            DecisionEdge(target="node-a"),
        ])
        content = messages[1]["content"]
        assert "## Context" not in content

    def test_includes_tool_call_instruction(self) -> None:
        messages = build_decision_messages(None, {}, [
            DecisionEdge(target="node-a"),
        ])
        assert "make_decision" in messages[1]["content"]


# --- Model resolution (US-003) ---


class TestResolveModel:
    def test_node_model_takes_precedence(self) -> None:
        config = DecisionNodeConfig(
            mode=DecisionMode.AI_BOUNDED,
            confidence_threshold=0.8,
            model="gpt-4o",
            edges=[DecisionEdge(target="a")],
        )
        assert resolve_model(config, "claude-sonnet-4-20250514") == "gpt-4o"

    def test_falls_back_to_default(self) -> None:
        config = DecisionNodeConfig(
            mode=DecisionMode.AI_BOUNDED,
            confidence_threshold=0.8,
            edges=[DecisionEdge(target="a")],
        )
        assert resolve_model(config, "claude-sonnet-4-20250514") == "claude-sonnet-4-20250514"


# --- Response parsing (US-003) ---


class _FakeFunction:
    def __init__(self, arguments: str) -> None:
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, arguments: str) -> None:
        self.function = _FakeFunction(arguments)


class _FakeMessage:
    def __init__(
        self,
        tool_calls: list[_FakeToolCall] | None = None,
        content: str | None = None,
    ) -> None:
        self.tool_calls = tool_calls
        self.content = content


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, choices: list[_FakeChoice]) -> None:
        self.choices = choices


class TestParseAIResponse:
    def test_parse_tool_call(self) -> None:
        args = json.dumps({
            "selected_edge_target": "node-a",
            "confidence": 0.9,
            "reasoning": "Clear match",
        })
        response = _FakeResponse([
            _FakeChoice(_FakeMessage(tool_calls=[_FakeToolCall(args)]))
        ])
        result = parse_ai_response(response)
        assert result.selected_edge_target == "node-a"
        assert result.confidence == 0.9

    def test_parse_text_fallback(self) -> None:
        text = json.dumps({
            "selected_edge_target": "node-b",
            "confidence": 0.7,
            "reasoning": "Fallback parse",
        })
        response = _FakeResponse([
            _FakeChoice(_FakeMessage(content=text))
        ])
        result = parse_ai_response(response)
        assert result.selected_edge_target == "node-b"
        assert result.confidence == 0.7

    def test_parse_text_with_markdown_fences(self) -> None:
        text = '```json\n{"selected_edge_target": "node-c", "confidence": 0.6, "reasoning": "Fenced"}\n```'
        response = _FakeResponse([
            _FakeChoice(_FakeMessage(content=text))
        ])
        result = parse_ai_response(response)
        assert result.selected_edge_target == "node-c"

    def test_parse_failure_raises_error(self) -> None:
        response = _FakeResponse([
            _FakeChoice(_FakeMessage(content="I don't know"))
        ])
        with pytest.raises(DecisionEvaluationError, match="Could not parse"):
            parse_ai_response(response)

    def test_malformed_json_tool_call_falls_to_text(self) -> None:
        text = json.dumps({
            "selected_edge_target": "node-d",
            "confidence": 0.5,
            "reasoning": "From text",
        })
        response = _FakeResponse([
            _FakeChoice(_FakeMessage(
                tool_calls=[_FakeToolCall("{bad json")],
                content=text,
            ))
        ])
        result = parse_ai_response(response)
        assert result.selected_edge_target == "node-d"

    def test_empty_tool_calls_falls_to_text(self) -> None:
        text = json.dumps({
            "selected_edge_target": "node-e",
            "confidence": 0.8,
            "reasoning": "Empty tool_calls",
        })
        response = _FakeResponse([
            _FakeChoice(_FakeMessage(tool_calls=[], content=text))
        ])
        result = parse_ai_response(response)
        assert result.selected_edge_target == "node-e"


# --- DECISION_TOOL constant (US-003) ---


class TestDecisionTool:
    def test_tool_structure(self) -> None:
        assert DECISION_TOOL["type"] == "function"
        func = DECISION_TOOL["function"]
        assert func["name"] == "make_decision"
        params = func["parameters"]
        assert "selected_edge_target" in params["properties"]
        assert "confidence" in params["properties"]
        assert "reasoning" in params["properties"]
        assert set(params["required"]) == {
            "selected_edge_target", "confidence", "reasoning",
        }


# --- DecisionResult: escalated and ai_recommendation fields (US-004) ---


class TestDecisionResultNewFields:
    def test_defaults(self) -> None:
        result = DecisionResult(
            selected_edge="node-a", mode="deterministic", confidence=1.0
        )
        assert result.escalated is False
        assert result.ai_recommendation is None

    def test_escalated_with_recommendation(self) -> None:
        ai_resp = AIDecisionResponse(
            selected_edge_target="node-a", confidence=0.5, reasoning="Low"
        )
        result = DecisionResult(
            selected_edge="node-a",
            mode="ai_bounded",
            confidence=0.5,
            escalated=True,
            ai_recommendation=ai_resp,
        )
        assert result.escalated is True
        assert result.ai_recommendation is not None
        assert result.ai_recommendation.confidence == 0.5


# --- AI Bounded and AI Autonomous modes (US-004) ---


def _make_ai_decision_node(
    mode: DecisionMode,
    edges: list[DecisionEdge],
    confidence_threshold: float = 0.8,
    node_id: str = "ai-decide-1",
    context_prompt: str | None = None,
    model: str | None = None,
) -> NodeDefinition:
    """Helper to create an AI decision node."""
    return NodeDefinition(
        id=node_id,
        type=NodeType.DECISION,
        label="AI Decision",
        config=DecisionNodeConfig(
            mode=mode,
            confidence_threshold=confidence_threshold,
            edges=edges,
            context_prompt=context_prompt,
            model=model,
        ),
    )


def _fake_litellm_response(
    selected_edge_target: str,
    confidence: float,
    reasoning: str,
) -> _FakeResponse:
    """Build a fake LiteLLM response with a tool call."""
    args = json.dumps({
        "selected_edge_target": selected_edge_target,
        "confidence": confidence,
        "reasoning": reasoning,
    })
    return _FakeResponse([
        _FakeChoice(_FakeMessage(tool_calls=[_FakeToolCall(args)]))
    ])


class TestDecisionEngineAIBounded:
    @pytest.fixture
    def engine(self) -> DecisionEngine:
        return DecisionEngine(default_model="test-model")

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_calls_litellm_with_correct_args(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        mock_acompletion.return_value = _fake_litellm_response(
            "node-a", 0.9, "Clear match"
        )
        node = _make_ai_decision_node(
            mode=DecisionMode.AI_BOUNDED,
            edges=[
                DecisionEdge(target="node-a", label="A"),
                DecisionEdge(target="node-b", label="B"),
            ],
            context_prompt="Pick the best route",
        )
        await engine.evaluate(node, {"status": "ready"})

        mock_acompletion.assert_called_once()
        call_kwargs = mock_acompletion.call_args
        assert call_kwargs.kwargs["model"] == "test-model"
        assert call_kwargs.kwargs["tools"] == [DECISION_TOOL]
        assert call_kwargs.kwargs["tool_choice"] == {
            "type": "function",
            "function": {"name": "make_decision"},
        }
        messages = call_kwargs.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert "Pick the best route" in messages[1]["content"]

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_valid_edge_above_threshold(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        mock_acompletion.return_value = _fake_litellm_response(
            "node-a", 0.9, "High confidence"
        )
        node = _make_ai_decision_node(
            mode=DecisionMode.AI_BOUNDED,
            edges=[DecisionEdge(target="node-a"), DecisionEdge(target="node-b")],
            confidence_threshold=0.8,
        )
        result = await engine.evaluate(node, {"x": 1})
        assert result.selected_edge == "node-a"
        assert result.mode == "ai_bounded"
        assert result.confidence == 0.9
        assert result.escalated is False
        assert result.ai_recommendation is not None
        assert result.ai_recommendation.selected_edge_target == "node-a"

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_confidence_below_threshold_escalates(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        mock_acompletion.return_value = _fake_litellm_response(
            "node-a", 0.5, "Low confidence"
        )
        node = _make_ai_decision_node(
            mode=DecisionMode.AI_BOUNDED,
            edges=[DecisionEdge(target="node-a"), DecisionEdge(target="node-b")],
            confidence_threshold=0.8,
        )
        result = await engine.evaluate(node, {"x": 1})
        assert result.escalated is True
        assert result.selected_edge == "node-a"
        assert result.confidence == 0.5
        assert result.ai_recommendation is not None
        assert result.ai_recommendation.reasoning == "Low confidence"

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_invalid_edge_target_raises_error(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        mock_acompletion.return_value = _fake_litellm_response(
            "node-invalid", 0.9, "Wrong target"
        )
        node = _make_ai_decision_node(
            mode=DecisionMode.AI_BOUNDED,
            edges=[DecisionEdge(target="node-a"), DecisionEdge(target="node-b")],
        )
        with pytest.raises(DecisionEvaluationError, match="invalid edge target"):
            await engine.evaluate(node, {"x": 1})

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_uses_node_model(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        mock_acompletion.return_value = _fake_litellm_response(
            "node-a", 0.9, "OK"
        )
        node = _make_ai_decision_node(
            mode=DecisionMode.AI_BOUNDED,
            edges=[DecisionEdge(target="node-a")],
            model="gpt-4o",
        )
        await engine.evaluate(node, {})
        assert mock_acompletion.call_args.kwargs["model"] == "gpt-4o"


class TestDecisionEngineAIAutonomous:
    @pytest.fixture
    def engine(self) -> DecisionEngine:
        return DecisionEngine(default_model="test-model")

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_valid_edge_above_threshold(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        mock_acompletion.return_value = _fake_litellm_response(
            "node-b", 0.95, "Very confident"
        )
        node = _make_ai_decision_node(
            mode=DecisionMode.AI_AUTONOMOUS,
            edges=[DecisionEdge(target="node-a"), DecisionEdge(target="node-b")],
            confidence_threshold=0.8,
        )
        result = await engine.evaluate(node, {"x": 1})
        assert result.selected_edge == "node-b"
        assert result.mode == "ai_autonomous"
        assert result.escalated is False
        assert result.ai_recommendation is not None

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_confidence_below_threshold_escalates(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        mock_acompletion.return_value = _fake_litellm_response(
            "node-a", 0.3, "Very uncertain"
        )
        node = _make_ai_decision_node(
            mode=DecisionMode.AI_AUTONOMOUS,
            edges=[DecisionEdge(target="node-a"), DecisionEdge(target="node-b")],
            confidence_threshold=0.8,
        )
        result = await engine.evaluate(node, {"x": 1})
        assert result.escalated is True
        assert result.ai_recommendation is not None
        assert result.ai_recommendation.confidence == 0.3

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_invalid_edge_target_raises_error(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        mock_acompletion.return_value = _fake_litellm_response(
            "nonexistent", 0.9, "Bad edge"
        )
        node = _make_ai_decision_node(
            mode=DecisionMode.AI_AUTONOMOUS,
            edges=[DecisionEdge(target="node-a")],
        )
        with pytest.raises(DecisionEvaluationError, match="invalid edge target"):
            await engine.evaluate(node, {})

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_confidence_at_threshold_not_escalated(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        """Confidence exactly at threshold should NOT escalate (< check, not <=)."""
        mock_acompletion.return_value = _fake_litellm_response(
            "node-a", 0.8, "Exactly at threshold"
        )
        node = _make_ai_decision_node(
            mode=DecisionMode.AI_AUTONOMOUS,
            edges=[DecisionEdge(target="node-a")],
            confidence_threshold=0.8,
        )
        result = await engine.evaluate(node, {})
        assert result.escalated is False


# --- DecisionResult: checkpoint_prompt field (US-005) ---


class TestDecisionResultCheckpointPrompt:
    def test_default_none(self) -> None:
        result = DecisionResult(
            selected_edge="node-a", mode="deterministic", confidence=1.0
        )
        assert result.checkpoint_prompt is None

    def test_checkpoint_prompt_set(self) -> None:
        result = DecisionResult(
            selected_edge="node-a",
            mode="ai_checkpoint",
            confidence=0.9,
            checkpoint_prompt="Please confirm this decision",
        )
        assert result.checkpoint_prompt == "Please confirm this decision"


# --- AI Checkpoint mode (US-005) ---


class TestDecisionEngineAICheckpoint:
    @pytest.fixture
    def engine(self) -> DecisionEngine:
        return DecisionEngine(default_model="test-model")

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_always_escalates_above_threshold(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        """Checkpoint mode always escalates, even when confidence is high."""
        mock_acompletion.return_value = _fake_litellm_response(
            "node-a", 0.95, "Very confident"
        )
        node = _make_ai_decision_node(
            mode=DecisionMode.AI_CHECKPOINT,
            edges=[DecisionEdge(target="node-a"), DecisionEdge(target="node-b")],
            confidence_threshold=0.8,
        )
        result = await engine.evaluate(node, {"x": 1})
        assert result.escalated is True
        assert result.selected_edge == "node-a"
        assert result.mode == "ai_checkpoint"
        assert result.confidence == 0.95

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_always_escalates_below_threshold(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        """Checkpoint mode escalates even when confidence is below threshold."""
        mock_acompletion.return_value = _fake_litellm_response(
            "node-b", 0.3, "Low confidence"
        )
        node = _make_ai_decision_node(
            mode=DecisionMode.AI_CHECKPOINT,
            edges=[DecisionEdge(target="node-a"), DecisionEdge(target="node-b")],
            confidence_threshold=0.8,
        )
        result = await engine.evaluate(node, {"x": 1})
        assert result.escalated is True
        assert result.confidence == 0.3

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_always_escalates_at_threshold(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        """Checkpoint mode escalates even at exact threshold."""
        mock_acompletion.return_value = _fake_litellm_response(
            "node-a", 0.8, "At threshold"
        )
        node = _make_ai_decision_node(
            mode=DecisionMode.AI_CHECKPOINT,
            edges=[DecisionEdge(target="node-a")],
            confidence_threshold=0.8,
        )
        result = await engine.evaluate(node, {})
        assert result.escalated is True

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_includes_ai_recommendation(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        """AI recommendation is included in the result."""
        mock_acompletion.return_value = _fake_litellm_response(
            "node-a", 0.9, "Strong signal"
        )
        node = _make_ai_decision_node(
            mode=DecisionMode.AI_CHECKPOINT,
            edges=[DecisionEdge(target="node-a")],
            confidence_threshold=0.8,
        )
        result = await engine.evaluate(node, {"status": "ready"})
        assert result.ai_recommendation is not None
        assert result.ai_recommendation.selected_edge_target == "node-a"
        assert result.ai_recommendation.confidence == 0.9
        assert result.ai_recommendation.reasoning == "Strong signal"

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_checkpoint_prompt_passed_through(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        """checkpoint_prompt from node config is passed through in result."""
        mock_acompletion.return_value = _fake_litellm_response(
            "node-a", 0.9, "OK"
        )
        node = NodeDefinition(
            id="cp-1",
            type=NodeType.DECISION,
            label="Checkpoint Decision",
            config=DecisionNodeConfig(
                mode=DecisionMode.AI_CHECKPOINT,
                confidence_threshold=0.8,
                edges=[DecisionEdge(target="node-a")],
                checkpoint_prompt="Do you approve this routing?",
            ),
        )
        result = await engine.evaluate(node, {})
        assert result.checkpoint_prompt == "Do you approve this routing?"

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_checkpoint_prompt_none_when_not_set(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        """checkpoint_prompt is None when not configured on the node."""
        mock_acompletion.return_value = _fake_litellm_response(
            "node-a", 0.9, "OK"
        )
        node = _make_ai_decision_node(
            mode=DecisionMode.AI_CHECKPOINT,
            edges=[DecisionEdge(target="node-a")],
            confidence_threshold=0.8,
        )
        result = await engine.evaluate(node, {})
        assert result.checkpoint_prompt is None

    @pytest.mark.asyncio
    @patch("roots.core.decision.litellm.acompletion", new_callable=AsyncMock)
    async def test_invalid_edge_target_raises_error(
        self, mock_acompletion: AsyncMock, engine: DecisionEngine
    ) -> None:
        """Invalid edge target raises DecisionEvaluationError."""
        mock_acompletion.return_value = _fake_litellm_response(
            "nonexistent", 0.9, "Bad target"
        )
        node = _make_ai_decision_node(
            mode=DecisionMode.AI_CHECKPOINT,
            edges=[DecisionEdge(target="node-a")],
        )
        with pytest.raises(DecisionEvaluationError, match="invalid edge target"):
            await engine.evaluate(node, {})
