"""The tenancy layer: slugs, registries, and the one place a path is built.

These are the tests that make the multi-tenant restructure hold. Most of them
pin a *refusal* -- a slug that cannot become a directory, a run id that cannot
climb into a neighbour, a config that fails loudly instead of falling back to a
template -- because the failure mode this layer guards against is not a crash.
It is one company's numbers being reported under another company's name.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kpi_engine.config_io import project_root
from kpi_engine.contracts.tenancy import CompanyRegistry, CompanySpec
from kpi_engine.tenancy import (
    CompanyConfigMissing,
    InvalidRunId,
    UnknownCompany,
    company_config,
    open_company,
)

DEMO = "acme-retail"
FIXTURE = "testco"


def _entry(**kw) -> dict:
    base = dict(company_id="a-co", display_name="A", domains=["retail"],
                created_at="2026-01-01")
    base.update(kw)
    return base


def _spec(**kw) -> dict:
    base = dict(
        company_id="a-co", display_name="A", domains=["retail"],
        created_at="2026-01-01",
        sources=[dict(source_id="s1", role="primary", domain="retail",
                      source="configs/sources/primary.yaml",
                      contract="configs/semantics/kpis.yaml")],
        configs=dict(graph="configs/causal/dag.yaml",
                     detection="configs/detection/default.yaml",
                     eda="configs/eda/default.yaml",
                     personas="configs/agent/personas.yaml"),
    )
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# The slug is the security boundary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "slug", ["../etc", "a/b", "Acme", "a", "", " co", "x" * 64, "-lead", "with space"]
)
def test_a_slug_that_could_become_a_path_is_rejected_by_the_model(slug):
    """A company id becomes a directory name and a URL segment.

    Validating it at the contract layer rather than at each use site is what makes
    traversal impossible everywhere at once: an invalid slug never reaches
    `Path()`, on the CLI or over HTTP.
    """
    with pytest.raises(ValidationError):
        CompanyRegistry(companies=[_entry(company_id=slug)])


def test_a_run_id_cannot_climb_out_of_its_company():
    paths = open_company(FIXTURE)
    with pytest.raises(InvalidRunId):
        paths.artefact("../acme-retail", "agent_report.json")
    with pytest.raises(InvalidRunId):
        paths.artefact("../../../etc", "passwd")


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #


def test_the_registry_refuses_two_companies_with_one_id():
    with pytest.raises(ValidationError):
        CompanyRegistry(companies=[_entry(), _entry(display_name="B")])


def test_the_registry_refuses_two_companies_in_one_folder():
    with pytest.raises(ValidationError):
        CompanyRegistry(companies=[_entry(path="shared"),
                                   _entry(company_id="b-co", path="shared")])


def test_one_supabase_user_cannot_belong_to_two_companies():
    """The lookup would otherwise be ambiguous exactly when it matters -- at
    upload time, deciding whose folder a file belongs in."""
    with pytest.raises(ValidationError):
        CompanyRegistry(companies=[
            _entry(external_ids={"supabase_user_ids": ["u-1"]}),
            _entry(company_id="b-co", external_ids={"supabase_user_ids": ["u-1"]}),
        ])


def test_an_unregistered_company_names_the_ones_that_exist():
    with pytest.raises(UnknownCompany) as exc:
        open_company("no-such-company")
    assert DEMO in str(exc.value)


# --------------------------------------------------------------------------- #
# The company spec
# --------------------------------------------------------------------------- #


def test_a_company_must_declare_exactly_one_primary_source():
    """`primary_source_id` is derived from the sources list, never declared beside
    it. Two primaries -- or none -- would make "which source answers a question by
    default" a coin flip."""
    two = _spec()["sources"] + [dict(source_id="s2", role="primary", domain="retail",
                                     source="a.yaml", contract="b.yaml")]
    with pytest.raises(ValidationError):
        CompanySpec.model_validate(_spec(sources=two))

    none = [{**_spec()["sources"][0], "role": "secondary"}]
    with pytest.raises(ValidationError):
        CompanySpec.model_validate(_spec(sources=none))


def test_a_company_cannot_declare_one_source_id_twice():
    dupes = _spec()["sources"] * 2
    with pytest.raises(ValidationError):
        CompanySpec.model_validate(_spec(sources=dupes))


def test_the_primary_is_derived_not_stored():
    spec = CompanySpec.model_validate(_spec())
    assert spec.primary_source_id == "s1"
    assert spec.secondary_source_ids() == []


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def test_a_missing_config_fails_loudly_and_says_where_it_looked():
    """No fallback to templates/ at run time. A silent fallback would mean two
    tenants sharing a DAG, and the verifier would then check one company's
    narrative against another company's causal graph."""
    paths = open_company(FIXTURE)
    broken = paths.spec.model_copy(
        update={"configs": paths.spec.configs.model_copy(
            update={"graph": "configs/causal/does-not-exist.yaml"})}
    )
    object.__setattr__(paths, "spec", broken)
    paths._cache.clear()
    try:
        with pytest.raises(CompanyConfigMissing) as exc:
            paths.config("graph")
        assert "does-not-exist.yaml" in str(exc.value)
        assert FIXTURE in str(exc.value)
    finally:
        # `open_company` memoises on mtime, so a mutated object would leak.
        from kpi_engine.tenancy import forget_company
        forget_company(FIXTURE)


def test_a_source_spec_comes_back_absolute_with_the_dataset_override_applied():
    """`SourceBinding.dataset` is why acme needs no second source YAML that says
    the same thing with one line changed."""
    paths = open_company(DEMO)
    spec = paths.source_spec("retail_daily")
    assert spec.path.startswith(str(paths.root))
    assert spec.path.endswith("ad_cost_shock_v1.csv")
    # And the base file, for scenario injection, is the un-overridden one.
    assert paths.base_source_spec("retail_daily").path.endswith("dummy_data.csv")


def test_the_causal_graph_is_parsed_once_per_company():
    """The agent used to re-read and re-construct the DAG six times in a run.

    Memoising is not only a speed fix: it means a run sees one graph, and a config
    edited mid-run cannot change the answer halfway through.
    """
    paths = open_company(DEMO)
    assert paths.graph() is paths.graph()
    assert paths.detection() is paths.detection()
    assert paths.eda() is paths.eda()


def test_company_config_carries_no_path_strings():
    """The old default config named six files positionally, which is what made
    "index 0 is sales, index 1 is supply chain" load-bearing. Paths are reached
    through the workspace object now, so there is nothing to index into."""
    cfg = company_config(open_company(DEMO))
    assert set(cfg) == {"company", "paths", "time_grain", "entity_keys", "top_events"}
    assert not any(isinstance(v, str) and "/" in v for v in cfg.values())


# --------------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------------- #


def test_two_companies_sharing_a_source_id_get_different_profile_caches():
    """`testco` deliberately declares `retail_daily`, the same id acme uses.

    The old cache was `outputs/profiles/<source_id>.json`, global and keyed on
    that name alone -- so one tenant's column profile, and therefore its
    redundancy findings, which gate what may act as a causal driver, would have
    been served to the other.
    """
    a, b = open_company(DEMO), open_company(FIXTURE)
    assert "retail_daily" in a.spec.source_ids and "retail_daily" in b.spec.source_ids
    assert a.profile_path("retail_daily") != b.profile_path("retail_daily")
    assert a.profile_path("retail_daily").is_relative_to(a.root)
    assert b.profile_path("retail_daily").is_relative_to(b.root)


def test_no_company_writes_outside_its_own_folder():
    paths = open_company(FIXTURE)
    for path in (paths.configs_dir, paths.data_dir, paths.raw_dir,
                 paths.generated_dir, paths.profile_path("retail_daily")):
        assert path.is_relative_to(paths.root)


# --------------------------------------------------------------------------- #
# latest_run_dir
# --------------------------------------------------------------------------- #


def test_latest_run_skips_directories_that_are_not_runs(tmp_path, monkeypatch):
    """Sorting `outputs/` by name and taking the last was already wrong: the
    profile cache was a sibling of the runs, and run ids are not uniformly
    timestamped, so name order was never time order."""
    import os
    import time

    monkeypatch.setenv("KPI_OUTPUTS_ROOT", str(tmp_path))
    paths = open_company(FIXTURE)
    out = paths.outputs_dir
    out.mkdir(parents=True, exist_ok=True)

    (out / "profiles").mkdir()                       # not a run
    (out / "_scratch").mkdir()                       # not a run
    (out / "zzz-no-marker").mkdir()                  # sorts last, still not a run
    for name in ("aaa-old", "mmm-new"):
        (out / name).mkdir()
        (out / name / "events.json").write_text("[]")
    os.utime(out / "aaa-old", (time.time() - 600, time.time() - 600))

    assert paths.latest_run_dir().name == "mmm-new"
    assert {d.name for d in paths.runs()} == {"aaa-old", "mmm-new"}


def test_no_runs_at_all_says_which_company_and_where(tmp_path, monkeypatch):
    monkeypatch.setenv("KPI_OUTPUTS_ROOT", str(tmp_path))
    paths = open_company(FIXTURE)
    with pytest.raises(FileNotFoundError) as exc:
        paths.latest_run_dir()
    assert FIXTURE in str(exc.value)


# --------------------------------------------------------------------------- #
# The invariant that keeps the restructure from unravelling
# --------------------------------------------------------------------------- #


def test_project_root_is_not_a_config_or_data_anchor():
    """`project_root()` locates `user/`, `templates/`, `schemas/` and `.env`.

    Nothing else. Every path to a dataset or a config is company-relative and goes
    through `CompanyPaths`. This test is what stops that becoming a convention
    people drift from: reintroducing a project-relative config path means
    importing `project_root` somewhere new, and that fails here.
    """
    allowed = {
        "kpi_engine/config_io.py",       # defines it
        "kpi_engine/tenancy.py",         # user_root() and templates_root()
        "kpi_engine/cli/export_schemas.py",  # schemas/ is a product artefact
        "kpi_api/app.py",                # the .env presence check on /health
        "kpi_agent/llm.py",              # load_dotenv(project_root() / ".env")
    }
    src = project_root() / "src"
    offenders = sorted(
        str(f.relative_to(src))
        for f in src.rglob("*.py")
        if "project_root" in f.read_text()
        and str(f.relative_to(src)) not in allowed
    )
    assert not offenders, (
        f"{offenders} reach for project_root(). A path to data or config must be "
        "company-relative and resolved through CompanyPaths."
    )


def test_only_tenancy_knows_where_the_package_sits_on_disk():
    """One `__file__`-derived anchor, in one module."""
    src = project_root() / "src"
    offenders = sorted(
        str(f.relative_to(src))
        for f in src.rglob("*.py")
        if "__file__" in f.read_text() and f.name != "config_io.py"
    )
    assert not offenders, f"{offenders} derive a path from __file__."
