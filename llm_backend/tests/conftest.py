import pandas as pd
import pytest

from kpi_engine.config_io import load_contract, load_graph, project_root


@pytest.fixture(scope="session")
def contract():
    return load_contract(project_root() / "configs/semantics/retail_kpis.yaml")


@pytest.fixture(scope="session")
def graph_spec():
    return load_graph(project_root() / "configs/causal/retail_dag.yaml")


@pytest.fixture
def relaxed_contract(contract):
    """The real contract with support guards lifted.

    The toy fixtures have one or two rows per cell by design, which the production
    guard correctly blanks. Tests about aggregation arithmetic need the arithmetic
    visible, so they relax the guard rather than pad the fixture with rows that
    would obscure what is being checked.
    """
    relaxed = contract.model_copy(deep=True)
    for kpi in relaxed.kpis:
        kpi.guards.min_rows_per_cell = 1
    return relaxed


@pytest.fixture
def toy_rows():
    """Two periods x two regions, with deliberately unequal denominators.

    The unequal denominators are the point: they are what makes ratio-of-sums and
    mean-of-ratios disagree, so a test built on balanced data would pass either way.
    """
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(
                ["2025-01-01"] * 2 + ["2025-01-02"] * 2
            ),
            "Region": ["North", "South", "North", "South"],
            "Sales Marketing Expenses": [100.0, 900.0, 200.0, 800.0],
            "New Customers": [10, 30, 20, 20],
            "Revenue From Ads": [300.0, 700.0, 400.0, 600.0],
            "Cost Of Ads": [100.0, 100.0, 100.0, 300.0],
            "Total Revenue": [1000.0, 2000.0, 1500.0, 2500.0],
            "Total Expenses": [800.0, 1000.0, 900.0, 2000.0],
            "Number of Sales": [10, 20, 15, 25],
            "Total Visitors": [100, 400, 150, 350],
            "COGS": [500.0, 900.0, 600.0, 1100.0],
            "Avg Inventory Value": [1000.0, 1000.0, 1000.0, 1000.0],
        }
    )
