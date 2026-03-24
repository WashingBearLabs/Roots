"""Tests for decision engine (US-001, US-002)."""

import pytest

from roots.core.decision import (
    DecisionEngine,
    DecisionEvaluationError,
    DecisionResult,
    evaluate_condition,
    flatten_for_eval,
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
        return DecisionEngine()

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
