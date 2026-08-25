"""Restricted arithmetic evaluator for KPI formulas.

KPI expressions are strings in config today and LLM-generated tomorrow, so
`eval` is not an option: a formula string is untrusted input. This module walks
the parsed AST and admits only arithmetic over declared measure aliases plus a
short whitelist of numeric functions. Anything else -- attribute access, calls
to unknown names, comprehensions, subscripts, lambdas -- is rejected before a
single value is computed.
"""

from __future__ import annotations

import ast
import math
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd


class ExpressionError(ValueError):
    """Raised when an expression is malformed or uses a disallowed construct."""


_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Call,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.USub,
    ast.UAdd,
)


def _safe_div(a: Any, b: Any) -> Any:
    """Division that yields NaN rather than raising or returning inf on a zero denominator."""
    a_arr = a if isinstance(a, (pd.Series, np.ndarray)) else np.asarray(a, dtype="float64")
    b_arr = b if isinstance(b, (pd.Series, np.ndarray)) else np.asarray(b, dtype="float64")
    if isinstance(b_arr, pd.Series):
        denom = b_arr.replace(0, np.nan)
    else:
        denom = np.where(b_arr == 0, np.nan, b_arr)
    with np.errstate(divide="ignore", invalid="ignore"):
        return a_arr / denom


_ALLOWED_FUNCS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "min": lambda *a: np.minimum.reduce(list(a)) if len(a) > 1 else a[0],
    "max": lambda *a: np.maximum.reduce(list(a)) if len(a) > 1 else a[0],
    "log": np.log,
    "exp": np.exp,
    "sqrt": np.sqrt,
    "safe_div": _safe_div,
}

_BINOPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: _safe_div,
    ast.Pow: lambda a, b: a**b,
    ast.Mod: lambda a, b: a % b,
}


def _parse(expression: str) -> ast.Expression:
    if not isinstance(expression, str) or not expression.strip():
        raise ExpressionError("Expression must be a non-empty string")
    if len(expression) > 1000:
        raise ExpressionError("Expression exceeds 1000 characters")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Cannot parse expression {expression!r}: {exc}") from exc
    return tree


def validate_expression(expression: str) -> ast.Expression:
    """Parse and structurally validate. Raises ExpressionError on anything disallowed."""
    tree = _parse(expression)
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(
                f"Disallowed construct {type(node).__name__} in expression {expression!r}"
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ExpressionError("Only direct calls to whitelisted functions are allowed")
            if node.func.id not in _ALLOWED_FUNCS:
                raise ExpressionError(
                    f"Function '{node.func.id}' is not whitelisted. "
                    f"Allowed: {sorted(_ALLOWED_FUNCS)}"
                )
            if node.keywords:
                raise ExpressionError("Keyword arguments are not allowed in expressions")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ExpressionError(
                f"Only numeric constants are allowed, got {type(node.value).__name__}"
            )
    return tree


def referenced_names(expression: str) -> set[str]:
    """Measure aliases the expression depends on, excluding whitelisted function names."""
    tree = validate_expression(expression)
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - called


def _eval_node(node: ast.AST, env: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise ExpressionError(f"Unknown measure '{node.id}'")
        return env[node.id]
    if isinstance(node, ast.BinOp):
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise ExpressionError(f"Disallowed operator {type(node.op).__name__}")
        return op(_eval_node(node.left, env), _eval_node(node.right, env))
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, env)
        return -operand if isinstance(node.op, ast.USub) else +operand
    if isinstance(node, ast.Call):
        func = _ALLOWED_FUNCS[node.func.id]  # validated already
        return func(*[_eval_node(a, env) for a in node.args])
    raise ExpressionError(f"Disallowed construct {type(node).__name__}")


def evaluate(expression: str, env: Mapping[str, Any]) -> Any:
    """Evaluate `expression` against a mapping of measure alias -> Series/scalar.

    Division by zero yields NaN rather than raising, so a KPI cell with an empty
    denominator becomes missing data that downstream guards can act on.
    """
    tree = validate_expression(expression)
    missing = referenced_names(expression) - set(env)
    if missing:
        raise ExpressionError(f"Expression {expression!r} needs undefined measures: {sorted(missing)}")
    result = _eval_node(tree, env)
    if isinstance(result, (pd.Series, np.ndarray)):
        return result
    return float(result) if not math.isnan(float(result)) else float("nan")
