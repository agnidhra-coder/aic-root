"""The expression evaluator is a security boundary: formulas are untrusted input
today from config and tomorrow from an LLM. These tests pin that boundary."""

import numpy as np
import pandas as pd
import pytest

from kpi_engine.semantics.expressions import (
    ExpressionError,
    evaluate,
    referenced_names,
    validate_expression,
)

MALICIOUS = [
    '__import__("os").system("rm -rf /")',
    'open("/etc/passwd").read()',
    "().__class__.__bases__[0].__subclasses__()",
    "x.__globals__",
    "[c for c in range(10)]",
    "(lambda: 1)()",
    "exec('x=1')",
    "eval('1+1')",
    "a if b else c",
    "x := 5",
    "print('hi')",
    "globals()",
]


@pytest.mark.parametrize("expression", MALICIOUS)
def test_rejects_dangerous_expressions(expression):
    with pytest.raises(ExpressionError):
        validate_expression(expression)


@pytest.mark.parametrize(
    "expression,env,expected",
    [
        ("a / b", {"a": 10.0, "b": 4.0}, 2.5),
        ("(a - b) / a * 100", {"a": 200.0, "b": 150.0}, 25.0),
        ("a * b + 2", {"a": 3.0, "b": 4.0}, 14.0),
        ("abs(a - b)", {"a": 3.0, "b": 10.0}, 7.0),
        ("sqrt(a)", {"a": 16.0}, 4.0),
    ],
)
def test_evaluates_arithmetic(expression, env, expected):
    assert evaluate(expression, env) == pytest.approx(expected)


def test_division_by_zero_becomes_nan_not_an_exception():
    """A zero denominator is missing data the guards can act on, not a crash."""
    out = evaluate("a / b", {"a": pd.Series([1.0, 2.0]), "b": pd.Series([2.0, 0.0])})
    assert out.iloc[0] == pytest.approx(0.5)
    assert np.isnan(out.iloc[1])


def test_unknown_measure_is_rejected():
    with pytest.raises(ExpressionError, match="undefined measures"):
        evaluate("a / missing", {"a": 1.0})


def test_referenced_names_excludes_function_names():
    assert referenced_names("abs(spend) / new_cust") == {"spend", "new_cust"}


def test_string_constants_rejected():
    with pytest.raises(ExpressionError):
        validate_expression("a + 'b'")


def test_contract_validation_catches_undefined_measure():
    """A KPI whose formula names a measure it never declared must not load."""
    from kpi_engine.contracts.configs import KpiDef

    with pytest.raises(ValueError, match="undefined measures"):
        KpiDef(
            name="Broken",
            measures={"a": {"column": "A", "agg": "sum"}},
            expression="a / b",
        )
