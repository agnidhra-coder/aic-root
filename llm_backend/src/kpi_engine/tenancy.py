"""Company workspaces: the one module that turns a config string into a path.

Everything a company owns -- its original data, its derived data, its KPI and
model configuration, and its outputs -- lives under one folder:

    user/<slug>/
      company.yaml
      configs/{agent,causal,detection,eda,scenarios,semantics,sources}/
      data/{raw,generated}/
      outputs/{profiles/, <run_id>/}

`outputs/` sits *inside* the company folder rather than being a shared directory
with a company subdirectory. That is what makes `latest_run_dir` unable to mistake
a tenant for a run, and what moves the API's traversal guard three levels deeper
instead of needing a second check beside it.

Two rules this module exists to enforce:

- **A path is company-relative or absolute; never project-relative.**
  `project_root()` locates `user/`, `templates/`, `schemas/` and `.env`. Nothing
  else. `CompanyPaths.resolve` is the only place a relative config string becomes
  a real path.
- **A missing config fails loudly.** There is no fallback to `templates/` at run
  time. A silent fallback would mean two tenants sharing a DAG, and the narrative
  verifier would then check one company's answer against another company's causal
  graph -- a wrong answer that looks like a right one.
"""

from __future__ import annotations

import argparse
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kpi_engine.causal.dag import CausalGraph
from kpi_engine.config_io import (
    load_company,
    load_contract,
    load_detection,
    load_eda,
    load_graph,
    load_personas,
    load_registry,
    load_scenario,
    load_source,
    project_root,
)
from kpi_engine.contracts.configs import (
    DetectionSpec,
    EdaSpec,
    KpiContract,
    ScenarioSpec,
    SourceSpec,
)
from kpi_engine.contracts.tenancy import (
    CompanyEntry,
    CompanyRegistry,
    CompanySpec,
    ConfigFamily,
    SourceBinding,
)

# Relocates `user/` for a deployed install, where the package lives in
# site-packages and the tenant data does not.
USER_ROOT_ENV = "KPI_USER_ROOT"

# Relocates every company's `outputs/` to <root>/<slug>. The test suite points
# this at a tmp dir so its fixed run ids stay meaningful without writing into the
# repository.
OUTPUTS_ROOT_ENV = "KPI_OUTPUTS_ROOT"

REGISTRY_FILENAME = "metadata.yaml"
COMPANY_FILENAME = "company.yaml"

# A directory under outputs/ is a run only if it carries one of these at its top
# level. Without the marker, `outputs/profiles/` and the per-source subdirectories
# the agent creates would both read as runs.
_RUN_MARKERS = ("events.json", "run_manifest.json", "agent_report.json")

# Serialises read-modify-write of user/metadata.yaml. Provisioning happens from
# CLI processes and from API worker threads, and the registry is one file.
_REGISTRY_LOCK = threading.Lock()


class UnknownCompany(KeyError):
    """No such company in the registry."""


class CompanyConfigMissing(FileNotFoundError):
    """A company declares a config file that is not on disk."""


class InvalidRunId(ValueError):
    """A run id that would escape the company's outputs directory."""


# --------------------------------------------------------------------------- registry


def user_root() -> Path:
    override = os.environ.get(USER_ROOT_ENV)
    return Path(override).expanduser().resolve() if override else project_root() / "user"


def templates_root() -> Path:
    return project_root() / "templates"


def registry_path() -> Path:
    return user_root() / REGISTRY_FILENAME


def read_registry() -> CompanyRegistry:
    path = registry_path()
    if not path.exists():
        raise CompanyConfigMissing(
            f"No company registry at {path}. Create one with "
            f"`python -m kpi_engine.cli.init_company --company <slug> ...`."
        )
    return load_registry(path)


def list_companies(active_only: bool = True) -> list[CompanyEntry]:
    entries = read_registry().companies
    return [e for e in entries if e.active] if active_only else list(entries)


def find_by_external_id(supabase_user_id: str) -> CompanyEntry | None:
    """Which company a Supabase `users.id` belongs to, or None.

    The registry guarantees at most one match, so this can never be ambiguous at
    the moment it matters -- deciding whose folder an upload lands in.
    """
    return read_registry().by_external_id(supabase_user_id)


def registry_lock() -> threading.Lock:
    """The lock guarding read-modify-write of the registry.

    Exposed so `create_company` can hold it across a read, a mutation and a write
    rather than around each separately.
    """
    return _REGISTRY_LOCK


# --------------------------------------------------------------------------- workspace


@dataclass
class CompanyPaths:
    """One company's workspace: where its things are, and what they load into.

    Config objects are memoised, which is not just a speed concern. The agent used
    to re-read and re-parse the causal DAG six times in a single run; going
    through one object means a run sees one graph, and a config file edited
    mid-run cannot change the answer halfway through.
    """

    slug: str
    root: Path
    entry: CompanyEntry
    spec: CompanySpec
    _cache: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    # ------------------------------------------------------------------ directories

    @property
    def configs_dir(self) -> Path:
        return self.root / "configs"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def generated_dir(self) -> Path:
        return self.data_dir / "generated"

    @property
    def outputs_dir(self) -> Path:
        override = os.environ.get(OUTPUTS_ROOT_ENV)
        if override:
            return Path(override).expanduser().resolve() / self.slug
        return self.root / "outputs"

    # ------------------------------------------------------------------ resolution

    def resolve(self, path: str | Path) -> Path:
        """The one place a config string becomes a filesystem path.

        Absolute paths pass through -- a caller who genuinely means a file outside
        the company folder (a scratch dataset, a mounted volume) says so by giving
        an absolute path. Everything else is company-relative.
        """
        p = Path(path)
        return p if p.is_absolute() else self.root / p

    def config(self, family: ConfigFamily) -> Path:
        path = self.resolve(getattr(self.spec.configs, family))
        if not path.exists():
            raise CompanyConfigMissing(
                f"Company '{self.slug}' declares {family} config "
                f"{getattr(self.spec.configs, family)!r}, but {path} does not exist. "
                f"Seed it from templates/company/*/configs/ or fix company.yaml."
            )
        return path

    # ------------------------------------------------------------------ outputs

    def run_dir(self, run_id: str, create: bool = True) -> Path:
        d = self.artefact_root() / run_id
        if create:
            d.mkdir(parents=True, exist_ok=True)
        return d

    def artefact_root(self) -> Path:
        return self.outputs_dir

    def profile_path(self, source_id: str) -> Path:
        """Where the cached data profile for one source lives.

        Under the company, not shared: two tenants may legitimately both call a
        source `retail_daily`, and a cache keyed on that name alone would serve one
        company's profile to the other.

        Under `data/`, not `outputs/`, and deliberately *not* redirected by
        `$KPI_OUTPUTS_ROOT`. A profile describes a file, not a run -- and building
        one is expensive: `find_linear_identities` searches signed combinations of
        up to three columns over ~38 columns, which takes minutes, not seconds.
        Putting it under the redirectable outputs root would make every test
        session rebuild every profile from scratch.
        """
        return self.data_dir / "profiles" / f"{source_id}.json"

    def artefact(self, run_id: str, name: str) -> Path:
        """A named file inside a run directory, guarded against traversal."""
        root = self.outputs_dir.resolve()
        path = (root / run_id / name).resolve()
        if not path.is_relative_to(root):
            raise InvalidRunId(
                f"Run id {run_id!r} resolves outside {self.slug}'s outputs directory."
            )
        return path

    # ------------------------------------------------------------------ onboarding

    def staging_path(self, source_id: str) -> Path:
        """Where an upload waits while its contract is being written.

        Deliberately **outside** every declared `SourceSpec.path`, which is the
        whole point: `missing_datasets` only looks at declared paths, so a staged
        file leaves the company `awaiting_data` and `/ask` keeps refusing it for
        the entire draft window. A tenant is never briefly answerable against a
        contract that does not match its data.

        `source_id` is caller-supplied, so it gets the same traversal guard
        `artefact` has.
        """
        root = (self.data_dir / "_staging").resolve()
        path = (root / f"{source_id}.csv").resolve()
        if not path.is_relative_to(root):
            raise InvalidRunId(
                f"Source id {source_id!r} resolves outside {self.slug}'s staging directory."
            )
        return path

    def draft_plan_path(self) -> Path:
        """The proposed KPI plan. Overwritten by each new proposal."""
        return self.configs_dir / "_draft" / "kpi_plan.json"

    def confirmed_plan_path(self) -> Path:
        """The decision and its provenance. `write_yaml` drops comments; this does not."""
        return self.configs_dir / "_confirmed" / "kpi_plan.json"

    def superseded_dir(self, plan_id: str) -> Path:
        """Where the configs a plan replaced are archived."""
        root = (self.configs_dir / "_superseded").resolve()
        path = (root / plan_id).resolve()
        if not path.is_relative_to(root):
            raise InvalidRunId(
                f"Plan id {plan_id!r} resolves outside {self.slug}'s archive directory."
            )
        return path

    def kpi_plan_state(self) -> str:
        """`none` | `drafted` | `confirmed`. Derived from disk, never stored."""
        if self.confirmed_plan_path().exists():
            return "confirmed"
        if self.draft_plan_path().exists():
            return "drafted"
        return "none"

    def staged_sources(self) -> dict[str, Path]:
        """Declared sources with an upload waiting for a contract, `source_id` -> path."""
        staged: dict[str, Path] = {}
        for binding in self.spec.sources:
            path = self.staging_path(binding.source_id)
            if path.exists():
                staged[binding.source_id] = path
        return staged

    def runs(self) -> list[Path]:
        """Run directories, oldest first.

        Ordered by mtime rather than by name: run ids are not uniformly
        timestamped (`ask-`, `pipeline-`, `pytest-api-*`, and whatever a caller
        supplies), so name order is not time order.
        """
        out = self.outputs_dir
        if not out.exists():
            return []
        candidates = [
            d
            for d in out.iterdir()
            if d.is_dir()
            and d.name != "profiles"
            and not d.name.startswith("_")
            and any((d / marker).exists() for marker in _RUN_MARKERS)
        ]
        return sorted(candidates, key=lambda d: d.stat().st_mtime)

    def latest_run_dir(self) -> Path:
        runs = self.runs()
        if not runs:
            raise FileNotFoundError(
                f"No completed runs under {self.outputs_dir} for company "
                f"'{self.slug}'. Run detect_anomalies first."
            )
        return runs[-1]

    # ------------------------------------------------------------------ readiness

    def missing_datasets(self) -> dict[str, Path]:
        """Declared sources whose file is not on disk yet, `source_id` -> path.

        A company legitimately exists before its data does: provisioning creates
        the configuration, and a CSV is attached afterwards.
        """
        missing: dict[str, Path] = {}
        for binding in self.spec.sources:
            try:
                path = self.source_spec(binding.source_id).path
            except CompanyConfigMissing:
                continue
            if not Path(path).exists():
                missing[binding.source_id] = Path(path)
        return missing

    @property
    def data_ready(self) -> bool:
        """Derived, never stored -- a status field in company.yaml would drift."""
        return not self.missing_datasets()

    # ------------------------------------------------------------------ typed loading

    @property
    def bindings(self) -> list[SourceBinding]:
        return list(self.spec.sources)

    @property
    def primary_source_id(self) -> str:
        return self.spec.primary_source_id

    def _load_yaml(self, key: str, rel: str, loader, what: str):
        if key in self._cache:
            return self._cache[key]
        path = self.resolve(rel)
        if not path.exists():
            raise CompanyConfigMissing(
                f"Company '{self.slug}' declares {what} {rel!r}, but {path} does not exist."
            )
        value = loader(path)
        self._cache[key] = value
        return value

    def source_spec(self, source_id: str) -> SourceSpec:
        """The source's spec with `path` already absolute.

        `SourceBinding.dataset` is folded in here, so no caller downstream needs to
        `model_copy` a path onto a spec -- and `build_source`'s relative-path
        branch never has to fire.
        """
        binding = self.spec.binding(source_id)
        spec: SourceSpec = self._load_yaml(
            f"source:{source_id}", binding.source, load_source, "source config"
        )
        dataset = binding.dataset or spec.path
        return spec.model_copy(update={"path": str(self.resolve(dataset))})

    def base_source_spec(self, source_id: str) -> SourceSpec:
        """The source as its own YAML declares it, ignoring the binding override.

        `source_spec` folds in `SourceBinding.dataset`, which is what a run wants.
        Scenario injection wants the opposite: the *base* file to inject into,
        because the override usually points at the output of a previous injection.
        """
        binding = self.spec.binding(source_id)
        spec: SourceSpec = self._load_yaml(
            f"source:{source_id}", binding.source, load_source, "source config"
        )
        return spec.model_copy(update={"path": str(self.resolve(spec.path))})

    def contract(self, source_id: str) -> KpiContract:
        binding = self.spec.binding(source_id)
        return self._load_yaml(
            f"contract:{source_id}", binding.contract, load_contract, "KPI contract"
        )

    def graph(self) -> CausalGraph:
        if "graph" not in self._cache:
            self._cache["graph"] = CausalGraph(load_graph(self.config("graph")))
        return self._cache["graph"]

    def detection(self) -> DetectionSpec:
        if "detection" not in self._cache:
            self._cache["detection"] = load_detection(self.config("detection"))
        return self._cache["detection"]

    def eda(self) -> EdaSpec:
        if "eda" not in self._cache:
            self._cache["eda"] = load_eda(self.config("eda"))
        return self._cache["eda"]

    def personas(self) -> dict[str, dict[str, Any]]:
        if "personas" not in self._cache:
            self._cache["personas"] = load_personas(self.config("personas"))
        return self._cache["personas"]

    def scenario(self, rel: str) -> ScenarioSpec:
        path = self.resolve(rel)
        if not path.exists():
            raise CompanyConfigMissing(
                f"Company '{self.slug}' has no scenario at {path}."
            )
        return load_scenario(path)

    # ------------------------------------------------------------------ validation

    def validate(self) -> list[str]:
        """Every declared path exists and loads. Returns problems, never raises.

        What `init_company --check` and `GET /company?company=...` both report.
        A source whose *dataset* is absent is not a problem here -- that is
        `missing_datasets`, and it is an expected state for a freshly created
        company.
        """
        problems: list[str] = []
        for family in ("graph", "detection", "eda", "personas"):
            try:
                {
                    "graph": self.graph,
                    "detection": self.detection,
                    "eda": self.eda,
                    "personas": self.personas,
                }[family]()
            except Exception as exc:  # noqa: BLE001 -- reporting, not handling
                problems.append(f"{family}: {exc}")

        for binding in self.spec.sources:
            try:
                spec = self.source_spec(binding.source_id)
                if spec.source_id != binding.source_id:
                    problems.append(
                        f"source {binding.source_id!r}: {binding.source} declares "
                        f"source_id {spec.source_id!r}; the two must match."
                    )
            except Exception as exc:  # noqa: BLE001
                problems.append(f"source {binding.source_id!r}: {exc}")
            try:
                contract = self.contract(binding.source_id)
                if contract.source_id != binding.source_id:
                    problems.append(
                        f"contract for {binding.source_id!r}: {binding.contract} "
                        f"declares source_id {contract.source_id!r}; the two must match."
                    )
            except Exception as exc:  # noqa: BLE001
                problems.append(f"contract for {binding.source_id!r}: {exc}")
        return problems


# --------------------------------------------------------------------------- opening

_OPEN_CACHE: dict[str, tuple[float, CompanyPaths]] = {}
_OPEN_LOCK = threading.Lock()


def company_root(entry: CompanyEntry) -> Path:
    return user_root() / entry.folder


def open_company(slug: str) -> CompanyPaths:
    """Load a company by slug, memoised on its `company.yaml` mtime.

    Memoised so a run parses each config once; keyed on mtime so editing a config
    between runs is picked up without restarting a server.
    """
    registry = read_registry()
    try:
        entry = registry.get(slug)
    except KeyError:
        known = [c.company_id for c in registry.companies]
        raise UnknownCompany(
            f"No company {slug!r} in {registry_path()}. Known: {known or '(none)'}"
        ) from None

    root = company_root(entry)
    company_file = root / COMPANY_FILENAME
    if not company_file.exists():
        raise CompanyConfigMissing(
            f"Company '{slug}' is registered but has no {COMPANY_FILENAME} at {company_file}."
        )

    mtime = company_file.stat().st_mtime
    with _OPEN_LOCK:
        cached = _OPEN_CACHE.get(slug)
        if cached and cached[0] == mtime:
            return cached[1]

    spec = load_company(company_file)
    if spec.company_id != slug:
        raise ValueError(
            f"{company_file} declares company_id {spec.company_id!r} but is registered "
            f"as {slug!r}. The registry and the company file must agree."
        )
    paths = CompanyPaths(slug=slug, root=root, entry=entry, spec=spec)
    with _OPEN_LOCK:
        _OPEN_CACHE[slug] = (mtime, paths)
    return paths


def forget_company(slug: str | None = None) -> None:
    """Drop memoised companies. For tests and for provisioning, which mutates disk."""
    with _OPEN_LOCK:
        if slug is None:
            _OPEN_CACHE.clear()
        else:
            _OPEN_CACHE.pop(slug, None)


# --------------------------------------------------------------------------- agent config


def company_config(paths: CompanyPaths) -> dict[str, Any]:
    """The config dict the agent graph runs on.

    Note what is *absent*: no path strings for sources, graph, detection, eda or
    personas. They are reached through `paths`, which is what removes the old
    positional two-source shape -- where index 0 meant the sales file and index 1
    the supply-chain file -- from the graph, the CLI and the API in one move.

    `paths` sits here beside the equally unserialisable `llm` and `usage` entries.
    Nothing dumps this dict: `kpi_api.events` names every field it projects.
    """
    return {
        "company": paths.slug,
        "paths": paths,
        "time_grain": paths.spec.agent.time_grain,
        "entity_keys": list(paths.spec.agent.entity_keys),
        "top_events": paths.spec.agent.top_events,
    }


# --------------------------------------------------------------------------- argparse


def add_company_argument(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """The required `--company` every CLI carries.

    Required, with no ambient default and no registry-level default company: a
    command that runs against whichever tenant happens to be configured is a
    command that reports one company's numbers under another's name.
    """
    parser.add_argument(
        "--company",
        required=True,
        help="Company slug (a folder under user/). See `list_companies`.",
    )
    return parser


def open_from_args(args: argparse.Namespace) -> CompanyPaths:
    return open_company(args.company)
