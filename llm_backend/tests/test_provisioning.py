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
        "/sources/data",
        files={"file": ("upload.csv", payload, "text/csv")},
        params={"company": company, "source_id": source},
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
    assert client.get("/company?company=demo-co").json()["status"] == "ready"


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
    assert client.get("/company?company=demo-co").json()["status"] == "awaiting_data"


def test_a_rejected_upload_leaves_the_previous_data_in_place(client, narrow_csv):
    """The file is staged beside its destination and validated before `os.replace`."""
    _create(client)
    _attach(client, narrow_csv)
    before = open_company("demo-co").source_spec("retail_daily").path
    size = Path(before).stat().st_size

    assert _attach(client, b"Date,Region\n2025-01-01,West\n").status_code == 422
    assert Path(before).stat().st_size == size
    assert client.get("/company?company=demo-co").json()["status"] == "ready"


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
        "/ask?company=demo-co", json={"question": "what needs attention?", "no_llm": True}
    )
    assert response.status_code == 409
    assert "awaiting data" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# Onboarding: deriving a contract from the file instead of the template
# --------------------------------------------------------------------------- #

# Deliberately narrower than any template's contract, and named so exact matching
# finds most of it. The retail contract needs `Avg Inventory Value`, `New
# Customers` and six more that are not here, so `POST .../sources/.../data` would
# refuse this file outright -- which is the whole reason onboarding exists.
ONBOARDING_CSV = (
    "Date,Region,Total Revenue,COGS,Total Expenses,Number of Sales,Total Visitors\n"
    + "\n".join(
        f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d},"
        f"{'West' if i % 2 else 'East'},{1000 + i},{400 + i},{700 + i},{50 + i},{900 + i}"
        for i in range(400)
    )
).encode()


def _plan(client, company="demo-co", payload=ONBOARDING_CSV, **params):
    return client.post(
        "/kpi-plan/sync",
        files={"file": ("upload.csv", payload, "text/csv")},
        params={"company": company, "no_llm": True, **params},
    )


def _blank(client, **kw):
    body = {"company_id": "demo-co", "display_name": "Demo Co",
            "domains": ["other"], "template": "blank"}
    body.update(kw)
    return client.post("/companies", json=body)


def test_a_blank_company_declares_no_kpis_until_a_plan_is_confirmed(client):
    """Seeding an unknown extract from `retail` would have it claim five KPIs it
    almost certainly cannot compute. An empty contract is the honest state."""
    assert _blank(client).status_code == 201
    paths = open_company("demo-co")
    assert paths.contract("primary").kpis == []
    assert paths.kpi_plan_state() == "none"


def test_a_csv_the_template_contract_could_never_accept_is_still_plannable(client):
    """The ordering problem onboarding exists to solve: the contract does not
    exist yet and is about to be written from this very file."""
    _create(client)  # the retail template, whose contract needs eight more columns
    assert _attach(client, ONBOARDING_CSV).status_code == 422
    body = _plan(client).json()
    assert body["done"]["outcome"] == "drafted"
    assert "Gross Profit Margin" in body["drafted"]["proposed"]


def test_a_staged_upload_does_not_make_the_company_look_ready(client):
    """`data/_staging/` is outside every declared source path, so a company with a
    plan in flight keeps reporting `awaiting_data` -- it is not briefly answerable
    against a contract that does not match its file."""
    _blank(client)
    _plan(client)
    assert client.get("/company?company=demo-co").json()["status"] == "awaiting_data"
    assert open_company("demo-co").staged_sources()


def test_asking_a_company_with_only_a_staged_upload_is_still_refused(client):
    _blank(client)
    _plan(client)
    response = client.post(
        "/ask?company=demo-co", json={"question": "what happened?", "no_llm": True}
    )
    assert response.status_code == 409


def test_confirming_a_plan_rewrites_the_contract_to_match_the_file(client):
    _blank(client)
    plan_id = _plan(client).json()["drafted"]["plan_id"]
    body = client.post(
        "/kpi-plan/confirm/sync?company=demo-co",
        json={"plan_id": plan_id, "no_llm": True, "warm_up": False},
    ).json()

    header = set(ONBOARDING_CSV.split(b"\n")[0].decode().split(","))
    contract = open_company("demo-co").contract("primary")
    assert contract.kpis
    for kpi in contract.kpis:
        for measure in kpi.measures.values():
            assert measure.column in header
    assert body["data"]["ready"] is True


def test_the_company_is_reopened_after_its_configs_are_rewritten(client):
    """`open_company` memoises on `company.yaml`'s mtime alone, so rewriting a
    semantics file without invalidating that cache would keep serving the KPIs the
    tenant had before the user changed them. No manual `forget_company` here --
    that is the point."""
    _blank(client)
    assert open_company("demo-co").contract("primary").kpis == []
    plan_id = _plan(client).json()["drafted"]["plan_id"]
    client.post(
        "/kpi-plan/confirm/sync?company=demo-co",
        json={"plan_id": plan_id, "no_llm": True, "warm_up": False},
    )
    assert open_company("demo-co").contract("primary").kpis


def test_a_confirmed_plan_archives_the_configs_it_replaced(client):
    """`write_yaml` uses `safe_dump`, so the generated files carry no comments and
    cannot say what they replaced. The archive and the confirmed plan can."""
    _blank(client)
    plan_id = _plan(client).json()["drafted"]["plan_id"]
    client.post(
        "/kpi-plan/confirm/sync?company=demo-co",
        json={"plan_id": plan_id, "no_llm": True, "warm_up": False},
    )
    paths = open_company("demo-co")
    archived = {p.name for p in paths.superseded_dir(plan_id).iterdir()}
    assert {"company-company.yaml", "contract-kpis.yaml"} <= archived
    assert paths.kpi_plan_state() == "confirmed"


def test_a_confirmed_plan_records_where_every_binding_came_from(client):
    _blank(client)
    plan_id = _plan(client).json()["drafted"]["plan_id"]
    client.post(
        "/kpi-plan/confirm/sync?company=demo-co",
        json={"plan_id": plan_id, "no_llm": True, "warm_up": False},
    )
    import json as _json

    confirmed = _json.loads(open_company("demo-co").confirmed_plan_path().read_text())
    sources = {
        m["bound_by"]
        for kpi in confirmed["draft"]["proposed"]
        for m in kpi["measures"]
    }
    assert sources == {"synonym"}
    assert confirmed["accepted"]


def test_a_plan_confirmed_with_every_kpi_rejected_is_refused_rather_than_written(client):
    """A company with an empty contract answers every question with silence."""
    _blank(client)
    drafted = _plan(client).json()["drafted"]
    response = client.post(
        "/kpi-plan/confirm/sync?company=demo-co",
        json={
            "plan_id": drafted["plan_id"],
            "no_llm": True,
            "decisions": [{"name": n, "verdict": "reject"} for n in drafted["proposed"]],
        },
    )
    assert response.status_code == 422
    assert "nothing to compute" in response.json()["detail"]
    assert open_company("demo-co").contract("primary").kpis == []


def test_a_stale_plan_id_is_refused_rather_than_merged_onto_the_current_draft(client):
    _blank(client)
    _plan(client)
    response = client.post(
        "/kpi-plan/confirm/sync?company=demo-co",
        json={"plan_id": "kpiplan-19700101-000000", "no_llm": True},
    )
    assert response.status_code == 409
    assert "not the current draft" in response.json()["detail"]


def test_confirming_with_no_draft_at_all_is_a_404(client):
    _blank(client)
    response = client.post(
        "/kpi-plan/confirm/sync?company=demo-co",
        json={"plan_id": "kpiplan-19700101-000000", "no_llm": True},
    )
    assert response.status_code == 404


def test_the_draft_can_be_read_back_by_a_caller_that_did_not_create_it(client):
    _blank(client)
    plan_id = _plan(client).json()["drafted"]["plan_id"]
    assert client.get("/kpi-plan?company=demo-co").json()["plan_id"] == plan_id


def test_a_company_with_no_draft_reports_404_rather_than_an_empty_plan(client):
    _blank(client)
    assert client.get("/kpi-plan?company=demo-co").status_code == 404


def test_every_template_dag_names_only_its_own_contracts_kpis():
    """A graph naming another domain's metrics licenses no explanation of this
    one's, and every KPI node must exist for `verify.py` to check a narrative
    against the right causal structure."""
    from kpi_engine.config_io import load_contract, load_graph
    from kpi_engine.provisioning import list_templates, templates_dir

    for name in list_templates():
        root = templates_dir() / name
        graph = load_graph(root / "configs" / "causal" / "dag.yaml")
        contract = load_contract(root / "configs" / "semantics" / "kpis.yaml")
        kpi_nodes = {n.name for n in graph.nodes if n.kind == "kpi"}
        assert kpi_nodes == {k.name for k in contract.kpis}, (
            f"template {name!r}: graph {graph.graph_id!r} declares KPI nodes "
            f"{sorted(kpi_nodes)} but its contract declares "
            f"{sorted(k.name for k in contract.kpis)}"
        )


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
