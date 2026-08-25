"""Aggregation semantics. The ratio-of-sums rule is the one that silently
produces wrong numbers if broken, so it is tested directly against the
alternative rather than only against a hardcoded expected value."""

import pandas as pd
import pytest

from kpi_engine.semantics.panel import build_panel, floor_to_grain


def test_ratio_of_sums_not_mean_of_ratios(relaxed_contract, toy_rows):
    panel = build_panel(toy_rows, relaxed_contract, "Date", kpis=["CAC"], entity_keys=[])
    day1 = panel.series("CAC").iloc[0]["value"]

    # Correct: sum the measures, then divide. (100 + 900) / (10 + 30)
    assert day1 == pytest.approx(1000.0 / 40.0)

    # The tempting alternative averages per-row ratios and gives a different answer.
    mean_of_ratios = ((100.0 / 10.0) + (900.0 / 30.0)) / 2
    assert mean_of_ratios == pytest.approx(20.0)
    assert day1 != pytest.approx(mean_of_ratios)


def test_entity_slicing_partitions_the_data(relaxed_contract, toy_rows):
    panel = build_panel(toy_rows, relaxed_contract, "Date", kpis=["CAC"], entity_keys=["Region"])
    north = panel.series("CAC", {"Region": "North"})
    assert len(north) == 2
    assert north.iloc[0]["value"] == pytest.approx(100.0 / 10.0)


def test_support_counts_source_rows(relaxed_contract, toy_rows):
    panel = build_panel(toy_rows, relaxed_contract, "Date", kpis=["CAC"], entity_keys=[])
    assert panel.series("CAC")["_support"].tolist() == [2, 2]


def test_guard_blanks_cells_below_min_support(contract, toy_rows):
    strict = contract.model_copy(deep=True)
    strict.kpi("CAC").guards.min_rows_per_cell = 5
    panel = build_panel(toy_rows, strict, "Date", kpis=["CAC"], entity_keys=[])
    assert panel.series("CAC")["value"].isna().all()


def test_zero_denominator_yields_nan_cell(contract):
    rows = pd.DataFrame({
        "Date": pd.to_datetime(["2025-01-01"]),
        "Sales Marketing Expenses": [100.0],
        "New Customers": [0],
    })
    relaxed = contract.model_copy(deep=True)
    relaxed.kpi("CAC").guards.min_rows_per_cell = 1
    panel = build_panel(rows, relaxed, "Date", kpis=["CAC"], entity_keys=[])
    assert panel.series("CAC")["value"].isna().all()


@pytest.mark.parametrize(
    "grain,expected",
    [("day", "2025-03-05"), ("week", "2025-03-03"), ("month", "2025-03-01")],
)
def test_floor_to_grain(grain, expected):
    out = floor_to_grain(pd.Series(pd.to_datetime(["2025-03-05"])), grain)
    assert out.iloc[0] == pd.Timestamp(expected)
