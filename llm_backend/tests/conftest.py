import pandas as pd
import pytest

from kpi_engine.config_io import load_graph
from kpi_engine.tenancy import open_company

# The two companies the suite runs against, and why there are two.
#
# `acme-retail` is the demo tenant: the real contract, the real DAG, the injected
# dataset the detector was calibrated on. Anything needing real signal, or
# asserting on a real KPI name, uses it -- a duplicate fixture contract would
# drift from the real one the first time a KPI is added.
#
# `testco` exists to test tenancy itself. It deliberately declares a source called
# `retail_daily`, the same id acme uses, so that anything sharing state by source
# id alone shows up as a failure rather than as a plausible-looking number.
DEMO_COMPANY = "acme-retail"
FIXTURE_COMPANY = "testco"


@pytest.fixture(scope="session", autouse=True)
def isolated_outputs(tmp_path_factory):
    """Send run artefacts to a tmp directory for the whole session.

    The suite uses fixed run ids (`pytest-agent`, `pytest-api-*`) on purpose: two
    tests assert on the id, and the stream/invoke parity test needs two runs to
    agree on one. Redirecting the root rather than randomising the ids keeps that
    coverage while keeping the repository clean.

    Only `outputs/` moves. The profile cache lives under `data/profiles/` and stays
    where it is -- building one searches signed combinations of up to three columns
    over ~38 columns and takes minutes, so rebuilding it every session would be the
    dominant cost of running the tests.
    """
    import os

    root = tmp_path_factory.mktemp("outputs")
    os.environ["KPI_OUTPUTS_ROOT"] = str(root)
    yield root
    os.environ.pop("KPI_OUTPUTS_ROOT", None)


@pytest.fixture(scope="session")
def demo():
    return open_company(DEMO_COMPANY)


@pytest.fixture(scope="session")
def fixture_company():
    return open_company(FIXTURE_COMPANY)


@pytest.fixture(scope="session")
def contract(demo):
    return demo.contract(demo.primary_source_id)


@pytest.fixture(scope="session")
def graph_spec(demo):
    return load_graph(demo.config("graph"))


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
