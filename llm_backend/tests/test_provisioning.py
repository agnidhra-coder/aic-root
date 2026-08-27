"""Creating a company, and giving it data -- through both front-ends.

`cli.init_company` and `POST /companies` are two wrappers over one pair of
functions, so these tests exercise the HTTP route and trust the CLI to be the
same code path. What is pinned is mostly the refusals: a duplicate slug, a slug
that could become a path, a CSV that does not carry the columns the company's
contract names, and a question asked of a company whose data has not arrived.

Everything runs against a temporary `$KPI_USER_ROOT`, so the real `user/` is
never touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kpi_engine.tenancy import forget_company, open_company

# A CSV carrying exactly the columns the retail contract names, and no more.
#
# Not laziness -- `find_linear_identities` searches signed combinations of up to
# three columns, so profiling cost is combinatorial in *column count*: the full
# 38-column extract takes minutes, this takes seconds. It also exercises the
# warning path, because the retail DAG names supply-chain columns a sales-only
# file legitimately does not carry.
SOURCE_CSV = "user/acme-retail/data/raw/dummy_data.csv"


@pytest.fixture(scope="module")
def narrow_csv() -> bytes:
    import pandas as pd

    from kpi_engine.provisioning import required_columns

    needed = required_columns(open_company("acme-retail"), "retail_daily")
    return pd.read_csv(SOURCE_CSV)[needed].to_csv(index=False).encode()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """An empty tenant root with a valid, empty registry."""
    root = tmp_path / "user"
    root.mkdir()
    (root / "metadata.yaml").write_text('schema_version: "1.0"\ncompanies: []\n')
    monkeypatch.setenv("KPI_USER_ROOT", str(root))
    forget_company()
    yield root
    forget_company()


@pytest.fixture
def client(sandbox):
    from kpi_api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _create(client, **kw):
    body = {"company_id": "demo-co", "display_name": "Demo Co", "domains": ["retail"]}
    body.update(kw)
    return client.post("/companies", json=body)


def _attach(client, payload, company="demo-co", source="retail_daily"):
    return client.post(
        f"/companies/{company}/sources/{source}/data",
        files={"file": ("upload.csv", payload, "text/csv")},
    )


# --------------------------------------------------------------------------- #
# Creating
# --------------------------------------------------------------------------- #


def test_a_new_company_is_created_awaiting_data(client, sandbox):
    """A company exists before its data does.

    That ordering is the whole point of splitting creation from attachment: the
    NestJS tier can register a tenant the moment a user signs up, and forward
    their upload whenever it arrives.
    """
    response = _create(client)
    assert response.status_code == 201
    body = response.json()
    assert body["company_id"] == "demo-co"
    assert body["status"] == "awaiting_data"
    assert "retail_daily" in body["awaiting"]
    assert not body["problems"]

    assert (sandbox / "demo-co" / "company.yaml").exists()
    assert (sandbox / "demo-co" / "configs" / "semantics").is_dir()


def test_the_created_company_declares_the_templates_kpis_and_nothing_invented(client):
    """Provisioning copies a template's contract verbatim; it does not author KPIs.

    Choosing them per company is separate work, and having two places that decide
    what a company measures is how they come to disagree.
    """
    _create(client)
    paths = open_company("demo-co")
    contract = paths.contract("retail_daily")
    assert {k.name for k in contract.kpis} == {
        "CAC", "ROAS", "Net Profit Margin", "Conversion Rate", "Inventory Turnover"
    }


def test_a_duplicate_slug_is_refused_rather_than_overwritten(client):
    assert _create(client).status_code == 201
    assert _create(client, display_name="Impostor").status_code == 409


@pytest.mark.parametrize("slug", ["../etc", "Acme Co", "a", "with/slash"])
def test_a_slug_that_could_become_a_path_is_refused(client, slug):
    assert _create(client, company_id=slug).status_code == 422


def test_an_unknown_template_says_which_ones_exist(client):
    response = _create(client, template="no-such-template")
    assert response.status_code == 422
    assert "retail" in response.json()["detail"]


def test_a_supabase_id_cannot_be_claimed_by_two_companies(client):
    assert _create(client, supabase_user_ids=["u-1"]).status_code == 201
    clash = _create(client, company_id="other-co", supabase_user_ids=["u-1"])
    assert clash.status_code == 422
    # And the failed creation left nothing behind.
    assert "other-co" not in {c["company_id"] for c in client.get("/companies").json()}


def test_a_failed_creation_leaves_no_half_made_company(client, sandbox):
    _create(client, company_id="ghost-co", supabase_user_ids=["u-1"])
    _create(client, company_id="ghost-two", supabase_user_ids=["u-1"])
    assert not (sandbox / "ghost-two").exists()


# --------------------------------------------------------------------------- #
# Attaching data
# --------------------------------------------------------------------------- #


def test_attaching_a_matching_csv_makes_the_company_ready(client, narrow_csv):
    _create(client)
    response = _attach(client, narrow_csv)
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert client.get("/companies/demo-co").json()["status"] == "ready"


def test_the_profile_is_cached_at_attach_time(client, narrow_csv):
    """So the first real question does not pay for it, and so an unreadable file
    fails now rather than at query time."""
    _create(client)
    _attach(client, narrow_csv)
    assert open_company("demo-co").profile_path("retail_daily").exists()


def test_a_csv_missing_a_contract_column_is_refused_and_names_it(client, narrow_csv):
    """The check that makes the upload flow safe. A file without the columns a
    KPI's measures name cannot produce that KPI; accepting it would give an empty
    panel and a report saying nothing happened."""
    _create(client)
    response = _attach(client, b"Date,Region\n2025-01-01,West\n")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Total Revenue" in detail and "New Customers" in detail
    assert client.get("/companies/demo-co").json()["status"] == "awaiting_data"


def test_a_rejected_upload_leaves_the_previous_data_in_place(client, narrow_csv):
    """The file is staged beside its destination and validated before `os.replace`."""
    _create(client)
    _attach(client, narrow_csv)
    before = open_company("demo-co").source_spec("retail_daily").path
    size = Path(before).stat().st_size

    assert _attach(client, b"Date,Region\n2025-01-01,West\n").status_code == 422
    assert Path(before).stat().st_size == size
    assert client.get("/companies/demo-co").json()["status"] == "ready"


def test_an_undeclared_source_is_a_404(client, narrow_csv):
    _create(client)
    assert _attach(client, narrow_csv, source="not-a-source").status_code == 404


# --------------------------------------------------------------------------- #
# Asking
# --------------------------------------------------------------------------- #


def test_asking_a_company_with_no_data_is_refused_not_answered(client):
    """Running anyway would detect nothing and report a quiet period -- a wrong
    answer that looks like a right one, which is exactly what this engine's
    abstention rules exist to prevent."""
    _create(client)
    response = client.post(
        "/companies/demo-co/ask", json={"question": "what needs attention?", "no_llm": True}
    )
    assert response.status_code == 409
    assert "awaiting data" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# The registry under concurrency
# --------------------------------------------------------------------------- #


def test_two_concurrent_creations_both_land_in_the_registry(client):
    """`user/metadata.yaml` is read-modify-write and the API runs work on threads,
    so without the lock one creation would silently overwrite the other."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(
            lambda i: _create(client, company_id=f"co-{i}", display_name=f"Co {i}").status_code,
            range(4),
        ))
    assert results == [201] * 4
    assert {c["company_id"] for c in client.get("/companies").json()} == {
        f"co-{i}" for i in range(4)
    }
