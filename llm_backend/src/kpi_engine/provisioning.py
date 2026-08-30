"""Creating a company, and giving it data.

`tenancy.py` resolves a company that already exists; this creates one. Two verbs,
two modules, and exactly one implementation of each -- `cli.init_company` and the
`POST /companies` route are both thin wrappers over the functions here, for the
same reason `/ask` and `/ask/sync` both drain one graph run.

A company is created **before it has data**. Provisioning writes the
configuration -- which KPIs, which DAG, which detection thresholds -- and a CSV is
attached afterwards, by `attach_source_data`. That ordering is what lets the NestJS
tier register a tenant the moment a user signs up and forward their upload later.
Readiness is derived from the filesystem (`CompanyPaths.data_ready`), never stored,
so no status field can drift from what is actually on disk.

The validation that matters is in `attach_source_data`: every column a KPI's
measures name, plus the date column and every entity column, must exist in the
CSV header. A file that does not carry them is rejected *here*, loudly, rather
than producing an empty panel and a report that says nothing happened.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd

from kpi_engine.config_io import load_company, write_yaml
from kpi_engine.contracts.tenancy import (
    AgentDefaults,
    CompanyEntry,
    CompanyRegistry,
    CompanySpec,
    Domain,
    ExternalIds,
)
from kpi_engine.profiling import profile_source
from kpi_engine.sources import build_source
from kpi_engine.tenancy import (
    COMPANY_FILENAME,
    CompanyPaths,
    forget_company,
    open_company,
    read_registry,
    registry_lock,
    registry_path,
    templates_root,
    user_root,
)


class ProvisioningError(Exception):
    """A company could not be created or populated."""


class CompanyExists(ProvisioningError):
    """The slug is registered, or its folder is already on disk."""


class UnknownTemplate(ProvisioningError):
    """No such template under templates/company/."""


class DataRejected(ProvisioningError):
    """A CSV does not carry the columns this company's contract needs."""

    def __init__(self, message: str, missing: list[str]):
        super().__init__(message)
        self.missing = missing


# --------------------------------------------------------------------------- templates


def templates_dir() -> Path:
    return templates_root() / "company"


def list_templates() -> list[str]:
    root = templates_dir()
    if not root.exists():
        return []
    return sorted(d.name for d in root.iterdir() if (d / COMPANY_FILENAME).exists())


def load_template(name: str) -> tuple[Path, CompanySpec]:
    """A template is itself a `CompanySpec`, validated by the model that reads a
    live company. A template that stops parsing fails here rather than at some
    tenant's first question."""
    root = templates_dir() / name
    company_file = root / COMPANY_FILENAME
    if not company_file.exists():
        raise UnknownTemplate(
            f"No template {name!r} at {company_file}. Known: {list_templates() or '(none)'}"
        )
    return root, load_company(company_file)


def default_template_for(domains: Iterable[Domain]) -> str:
    available = set(list_templates())
    for domain in domains:
        if domain in available:
            return domain
    return "minimal" if "minimal" in available else next(iter(sorted(available)))


# --------------------------------------------------------------------------- create


def create_company(
    company_id: str,
    display_name: str,
    domains: list[Domain],
    *,
    template: str | None = None,
    agent: AgentDefaults | None = None,
    supabase_user_ids: Iterable[str] = (),
    notes: str = "",
    created_at: date | None = None,
    force: bool = False,
) -> CompanyPaths:
    """Create `user/<company_id>/` from a template and register it.

    Rolls the folder back if the company does not open and validate afterwards, so
    a failure never leaves a half-made tenant that later reads as real. Mirrors how
    the NestJS upload path removes an orphaned storage object when its database
    insert fails.
    """
    template_name = template or default_template_for(domains)
    template_dir, template_spec = load_template(template_name)

    root = user_root() / company_id
    created_here = False

    with registry_lock():
        registry = _read_registry_or_empty()

        if any(c.company_id == company_id for c in registry.companies):
            raise CompanyExists(f"Company {company_id!r} is already registered.")
        if root.exists() and not force:
            raise CompanyExists(
                f"{root} already exists. Refusing to overwrite an existing company folder."
            )
        for uid in supabase_user_ids:
            owner = registry.by_external_id(uid)
            if owner is not None:
                raise ProvisioningError(
                    f"supabase_user_id {uid!r} already belongs to {owner.company_id!r}."
                )

        try:
            if root.exists() and force:
                shutil.rmtree(root)
            shutil.copytree(template_dir / "configs", root / "configs")
            created_here = True
            for sub in ("data/raw", "data/generated", "outputs/profiles"):
                (root / sub).mkdir(parents=True, exist_ok=True)

            spec = template_spec.model_copy(
                update={
                    "company_id": company_id,
                    "display_name": display_name,
                    "domains": list(domains),
                    "created_at": created_at or date.today(),
                    "notes": notes,
                    "agent": agent or template_spec.agent,
                }
            )
            # Re-validate rather than trusting model_copy: it does not run
            # validators, and company_id is the field that must never be a path.
            spec = CompanySpec.model_validate(spec.model_dump(mode="json"))
            write_yaml(spec, root / COMPANY_FILENAME)

            entry = CompanyEntry(
                company_id=company_id,
                display_name=display_name,
                domains=list(domains),
                created_at=spec.created_at,
                external_ids=ExternalIds(supabase_user_ids=list(supabase_user_ids)),
            )
            updated = CompanyRegistry(
                schema_version=registry.schema_version,
                companies=[*registry.companies, entry],
            )
            write_yaml(updated, registry_path())
        except BaseException:
            if created_here:
                shutil.rmtree(root, ignore_errors=True)
            raise

    forget_company(company_id)
    try:
        paths = open_company(company_id)
        problems = paths.validate()
    except BaseException:
        _unregister(company_id)
        shutil.rmtree(root, ignore_errors=True)
        raise

    if problems:
        _unregister(company_id)
        shutil.rmtree(root, ignore_errors=True)
        raise ProvisioningError(
            f"Company {company_id!r} was created from template {template_name!r} but does "
            "not validate; it has been rolled back:\n  - " + "\n  - ".join(problems)
        )
    return paths


def reregister_company(company_id: str) -> CompanyPaths:
    """Re-add a registry entry for a company folder that already exists and
    validates, without touching its configs, data, or outputs.

    For the case where the folder and the registry have drifted apart: the
    tenant was created correctly at some point, but its `metadata.yaml` entry
    was lost independently (a reset or a revert that did not also touch
    `user/`). `create_company` refuses this folder with `CompanyExists`
    rather than silently reusing it -- callers that see that 409 alongside a
    registry lookup that says "not registered" should call this instead of
    `force=True`, which would `shutil.rmtree` real data.

    Raises `CompanyExists` if it is already registered, and `ProvisioningError`
    if the folder is missing or does not validate -- this never invents a
    company, only closes a registry gap for one already fully formed on disk.
    """
    root = user_root() / company_id
    company_file = root / COMPANY_FILENAME
    if not company_file.exists():
        raise ProvisioningError(
            f"No company folder at {root} to re-register -- use create_company instead."
        )
    spec = load_company(company_file)

    with registry_lock():
        registry = _read_registry_or_empty()
        if any(c.company_id == company_id for c in registry.companies):
            raise CompanyExists(f"Company {company_id!r} is already registered.")

        entry = CompanyEntry(
            company_id=spec.company_id,
            display_name=spec.display_name,
            domains=spec.domains,
            created_at=spec.created_at,
            external_ids=ExternalIds(supabase_user_ids=[]),
        )
        updated = CompanyRegistry(
            schema_version=registry.schema_version,
            companies=[*registry.companies, entry],
        )
        write_yaml(updated, registry_path())

    forget_company(company_id)
    try:
        paths = open_company(company_id)
        problems = paths.validate()
    except BaseException:
        _unregister(company_id)
        raise

    if problems:
        _unregister(company_id)
        raise ProvisioningError(
            f"Company {company_id!r}'s folder does not validate; not registered:\n  - "
            + "\n  - ".join(problems)
        )
    return paths


def _read_registry_or_empty() -> CompanyRegistry:
    if not registry_path().exists():
        return CompanyRegistry(companies=[])
    return read_registry()


def _unregister(company_id: str) -> None:
    """Drop a company from the registry. Used only to undo a failed creation."""
    with registry_lock():
        registry = _read_registry_or_empty()
        kept = [c for c in registry.companies if c.company_id != company_id]
        write_yaml(
            CompanyRegistry(schema_version=registry.schema_version, companies=kept),
            registry_path(),
        )
    forget_company(company_id)


# --------------------------------------------------------------------------- data


def required_columns(paths: CompanyPaths, source_id: str) -> list[str]:
    """Every column this source's contract cannot do without.

    The date column, the dimensions the panel may be sliced by, and every column a
    KPI's measures name. A CSV missing any of these cannot produce that KPI at all.
    """
    spec = paths.source_spec(source_id)
    contract = paths.contract(source_id)
    needed = {spec.date_column, *spec.entity_columns}
    for kpi in contract.kpis:
        needed.update(m.column for m in kpi.measures.values())
    return sorted(needed)


def stage_source_data(
    paths: CompanyPaths,
    source_id: str,
    *,
    csv_path: str | Path | None = None,
    content: bytes | None = None,
) -> tuple[Path, list[str], int]:
    """Park a CSV where a KPI plan can be built from it, without accepting it.

    The onboarding problem is an ordering problem: `attach_source_data` checks a
    file against the contract, but the whole point of onboarding is that the
    contract does not exist yet and is about to be written *from* the file. The
    tempting fix -- exposing `force=True` over HTTP -- deletes the guarantee that
    a company's data always matches its contract, and opens a window where
    `data_ready` is true and `/ask` answers against a contract naming columns the
    file does not carry.

    So the file goes somewhere no `SourceSpec` points at. `missing_datasets` sees
    nothing new, the company stays `awaiting_data`, and `/ask` keeps returning 409
    for the whole draft window. At confirm time the ordinary
    `attach_source_data` -- header gate and all -- moves it into place, and that
    gate now checks the file against the contract *we just synthesised from it*.
    A mismatch there means our own binder is wrong, which is exactly when we want
    to hear about it loudly.

    Returns the staged path, any warnings, and the row count.
    """
    if (csv_path is None) == (content is None):
        raise ValueError("Pass exactly one of `csv_path` or `content`.")

    paths.spec.binding(source_id)  # raises KeyError if the source is not declared
    target = paths.staging_path(source_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content if content is not None else Path(csv_path).read_bytes())

        # The only check here is that it is a readable CSV with a header and at
        # least one row. Anything about *columns* is the plan's business, not
        # this function's -- refusing a file for its columns is precisely what
        # onboarding exists to avoid.
        try:
            frame = pd.read_csv(tmp)
        except Exception as exc:  # noqa: BLE001 -- any parse failure is one answer
            raise DataRejected(f"The upload is not readable as CSV: {exc}", []) from exc
        if frame.empty:
            raise DataRejected("The upload has a header row but no data rows.", [])

        warnings: list[str] = []
        blank = [c for c in frame.columns if str(c).startswith("Unnamed:")]
        if blank:
            warnings.append(f"{len(blank)} column(s) have no header and cannot be used: {blank}")

        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    return target, warnings, len(frame)


def attach_source_data(
    paths: CompanyPaths,
    source_id: str,
    *,
    csv_path: str | Path | None = None,
    content: bytes | None = None,
    force: bool = False,
) -> tuple[CompanyPaths, list[str]]:
    """Put a CSV where a declared source expects it, then validate and profile it.

    Returns the reopened company and any warnings. The file is staged beside its
    destination and validated *before* `os.replace`, so a rejected upload leaves
    whatever was there untouched.

    `force` skips the column check. It exists for the case where a contract is
    about to be rewritten to match a file rather than the other way round; it is a
    CLI affordance and is not exposed over HTTP.

    Onboarding does **not** use it. That flow stages the file with
    `stage_source_data` and calls this function normally once the contract has
    been synthesised, so the header gate below runs against the new contract and
    catches a binder that produced one the file cannot satisfy.
    """
    if (csv_path is None) == (content is None):
        raise ValueError("Pass exactly one of `csv_path` or `content`.")

    spec = paths.source_spec(source_id)  # raises if the source is not declared
    target = Path(spec.path)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            if content is not None:
                fh.write(content)
            else:
                fh.write(Path(csv_path).read_bytes())

        warnings = _validate_csv(paths, source_id, tmp, force=force)
        # mkstemp creates 0600; the file is ordinary tenant data, not a secret.
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    forget_company(paths.slug)
    reopened = open_company(paths.slug)
    _cache_profile(reopened, source_id)
    return reopened, warnings


def _validate_csv(
    paths: CompanyPaths, source_id: str, path: Path, *, force: bool
) -> list[str]:
    try:
        header = list(pd.read_csv(path, nrows=0).columns)
    except Exception as exc:  # noqa: BLE001 -- any parse failure is the same answer
        raise DataRejected(f"{path.name} is not readable as CSV: {exc}", []) from exc

    missing = [c for c in required_columns(paths, source_id) if c not in header]
    if missing and not force:
        raise DataRejected(
            f"The file is missing {len(missing)} column(s) that "
            f"{paths.slug}/{source_id} needs: {missing}. "
            "Either supply a file that carries them, or change the contract.",
            missing,
        )

    warnings = [f"missing column {c!r} (--force)" for c in missing] if missing else []

    # A template DAG naming a column this tenant lacks is expected -- the graph is
    # generic, the extract is not. A *contract* naming one is not, which is why
    # that case is fatal above and this one is a warning.
    try:
        absent = sorted(
            node.column
            for node in paths.graph().spec.nodes
            if node.kind == "measure" and node.column and node.column not in header
        )
    except Exception:  # noqa: BLE001 -- the graph is validated separately
        absent = []
    if absent:
        warnings.append(
            f"causal graph names {len(absent)} column(s) this file does not carry, "
            f"so they cannot act as drivers: {absent}"
        )
    return warnings


def _cache_profile(paths: CompanyPaths, source_id: str) -> None:
    """Profile the new file now, so a first question does not pay for it -- and so
    an unreadable file fails at attach time rather than at query time."""
    from kpi_engine.config_io import write_json

    spec = paths.source_spec(source_id)
    source = build_source(spec, base_dir=paths.root)
    profile = profile_source(source, source.load())
    write_json(profile, paths.profile_path(source_id))
