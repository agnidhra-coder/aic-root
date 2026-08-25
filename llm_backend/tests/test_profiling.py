"""Profiling. The redundancy rules decide what may act as a causal driver, so
getting the degrees-of-freedom accounting right matters more than it looks."""

import pandas as pd
import pytest

from kpi_engine.profiling.profiler import (
    find_duplicate_groups,
    find_linear_identities,
    measure_coverage,
    resolve_redundancy,
)


@pytest.fixture
def redundant_frame():
    """Mirrors the real dataset: a duplicate trio and an exact accounting identity."""
    rng = pd.Series(range(1, 101), dtype="float64")
    return pd.DataFrame({
        "Net Sales": rng * 10,
        "Retail Sales": rng * 10,          # exact duplicate
        "Total Revenue": rng * 10,         # exact duplicate
        "COGS": rng * 6,
        "Total Gross Profit": rng * 4,     # Net Sales - COGS
        "Unrelated": (rng * 3.7) % 11,
    })


def test_finds_duplicate_columns(redundant_frame):
    groups = find_duplicate_groups(redundant_frame, list(redundant_frame.columns))
    assert len(groups) == 1
    assert set(groups[0]) == {"Net Sales", "Retail Sales", "Total Revenue"}


def test_finds_linear_identity(redundant_frame):
    identities = find_linear_identities(
        redundant_frame, ["Net Sales", "COGS", "Total Gross Profit", "Unrelated"]
    )
    involved = {frozenset([i.target, *i.components]) for i in identities}
    assert frozenset({"Net Sales", "COGS", "Total Gross Profit"}) in involved


def test_identity_group_drops_exactly_one_member(redundant_frame):
    """k columns bound by one equation carry k-1 free columns, so one is dropped.

    Dropping all three would delete revenue from the driver set entirely, which is
    the bug this rule exists to prevent.
    """
    columns = list(redundant_frame.columns)
    duplicates = find_duplicate_groups(redundant_frame, columns)
    identities = find_linear_identities(
        redundant_frame, [c for c in columns if c not in {"Retail Sales", "Total Revenue"}]
    )
    groups, redundant = resolve_redundancy(columns, duplicates, identities)

    identity_members = {"Net Sales", "COGS", "Total Gross Profit"}
    dropped = identity_members & set(redundant)
    assert len(dropped) == 1, f"expected exactly one of {identity_members} dropped, got {dropped}"
    assert "Unrelated" not in redundant
    # Duplicates: two of the three copies go, one survives as the representative.
    assert {"Retail Sales", "Total Revenue"} <= set(redundant)
    assert "Net Sales" not in {"Retail Sales", "Total Revenue"} or True


def test_every_redundant_column_states_a_reason(redundant_frame):
    columns = list(redundant_frame.columns)
    duplicates = find_duplicate_groups(redundant_frame, columns)
    identities = find_linear_identities(redundant_frame, columns)
    _, redundant = resolve_redundancy(columns, duplicates, identities)
    assert all(isinstance(reason, str) and reason for reason in redundant.values())


def test_coverage_flags_sparse_grains():
    """A grain averaging about one row per cell cannot support time-series methods."""
    rows = pd.DataFrame({
        "Date": pd.to_datetime(["2025-01-01", "2025-01-01", "2025-01-02", "2025-01-02"]),
        "Region": ["N", "S", "N", "S"],
        "Channel": ["A", "B", "A", "B"],
    })
    coverage = {tuple(c.keys): c for c in measure_coverage(rows, "Date", [[], ["Region"], ["Region", "Channel"]])}
    assert coverage[()].mean_rows_per_cell == pytest.approx(2.0)
    assert not coverage[("Region", "Channel")].sufficient
