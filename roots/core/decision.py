"""Safe expression evaluator for deterministic decision conditions."""

from __future__ import annotations

import re
from typing import Any

from simpleeval import (
    AttributeDoesNotExist,
    EvalWithCompoundTypes,
    NameNotDefined,
)


class DecisionEvaluationError(Exception):
    """Raised when a decision expression cannot be evaluated."""

    def __init__(self, expression: str, message: str) -> None:
        self.expression = expression
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
