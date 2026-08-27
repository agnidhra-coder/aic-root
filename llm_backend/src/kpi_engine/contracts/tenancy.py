"""Tenancy models: who a company is, and where its things live.

`configs.py` is one model per config *family* -- a source, a contract, a DAG.
Tenancy is a different axis: it says which set of those a run is allowed to see.
Keeping it in its own module means adding a company never touches the models that
describe an analysis.

Two files deserialise into what is here:

- `user/<slug>/company.yaml` -> `CompanySpec`. Every path inside it is
  *company-relative*, so a company folder can be copied, renamed, or mounted
  somewhere else without editing a line of YAML.
- `user/metadata.yaml` -> `CompanyRegistry`. A thin index -- display name,
  domains, external ids, active flag. Nothing that decides what *runs* lives
  here; that is `company.yaml`'s job, so the registry never has to be kept in
  step with a config change.

`CompanySlug` is the load-bearing one. It is validated at the contract layer
rather than at each use site, so a slug can never reach `Path()` as `../..`:
path traversal through a company selector fails in Pydantic, before any path is
built, on both the CLI and the API.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from kpi_engine import SCHEMA_VERSION
from kpi_engine.contracts.configs import Strict, TimeGrain

# Lowercase, digit, hyphen, underscore; must start alphanumeric; 2-63 characters.
# Deliberately narrower than the filesystem allows: this string becomes a
# directory name and a URL path segment, and it must be unambiguous in both.
# No `strip_whitespace`: " acme" must be rejected, not silently accepted as
# "acme". This id is a directory name, a URL segment and a registry key, and a
# value that means one thing to the caller and another on disk is worse than an
# error.
CompanySlug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{1,62}$")]

Domain = Literal["retail", "supply-chain", "marketing", "finance", "other"]
SourceRole = Literal["primary", "secondary"]

# The config families a company must declare a path for. Order is display order.
ConfigFamily = Literal["graph", "detection", "eda", "personas"]


class SourceBinding(Strict):
    """One source a company has declared.

    This replaces the positional two-entry `sources` list the agent used to carry,
    where index 0 meant "the sales file" and index 1 meant "the supply-chain
    file". Role and domain say what a source *is*; nothing depends on where it
    sits in a list, so a company may declare one source or five.
    """

    source_id: str = Field(
        description="Must equal the `source_id` inside the SourceSpec this points at. "
        "Checked when the company is opened, not here -- this model does not read files."
    )
    role: SourceRole = "secondary"
    domain: Domain
    source: str = Field(description="Company-relative path to the SourceSpec YAML.")
    contract: str = Field(description="Company-relative path to the KpiContract YAML.")
    dataset: str | None = Field(
        default=None,
        description="Company-relative override of SourceSpec.path. Lets one source "
        "definition point at a scenario-injected copy without editing its YAML.",
    )
    label: str = ""


class AgentDefaults(Strict):
    """What this company's questions default to when the caller says nothing.

    `default_persona` is a plain `str`, not `kpi_agent.models.Persona`: nothing in
    `kpi_engine` may import `kpi_agent`, and the persona set is validated where it
    is used, against the company's own personas.yaml.
    """

    time_grain: TimeGrain = "week"
    entity_keys: list[str] = Field(default_factory=list)
    top_events: int = Field(default=5, ge=1, le=50)
    default_persona: str | None = None


class CompanyConfigPaths(Strict):
    """Where the company's non-source configs live, company-relative.

    No defaults on purpose. `create_company` always writes all four explicitly, so
    a company's config set is readable from `company.yaml` alone without knowing
    the engine's directory conventions.
    """

    graph: str
    detection: str
    eda: str
    personas: str


class CompanySpec(Strict):
    """`user/<slug>/company.yaml` -- everything a run needs to know about a tenant."""

    company_id: CompanySlug
    display_name: str = Field(min_length=1)
    domains: list[Domain] = Field(min_length=1)
    created_at: date
    schema_version: str = SCHEMA_VERSION
    sources: list[SourceBinding] = Field(min_length=1)
    configs: CompanyConfigPaths
    agent: AgentDefaults = Field(default_factory=AgentDefaults)
    notes: str = ""

    @model_validator(mode="after")
    def _exactly_one_primary(self) -> CompanySpec:
        primaries = [b.source_id for b in self.sources if b.role == "primary"]
        if len(primaries) != 1:
            raise ValueError(
                f"Company '{self.company_id}' declares {len(primaries)} primary sources "
                f"({sorted(primaries)}); exactly one is required. The primary is the "
                "source a question is answered against by default."
            )
        return self

    @model_validator(mode="after")
    def _unique_source_ids(self) -> CompanySpec:
        ids = [b.source_id for b in self.sources]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"Company '{self.company_id}' repeats source ids: {dupes}")
        return self

    @property
    def primary_source_id(self) -> str:
        """Derived, never declared twice -- see `_exactly_one_primary`."""
        return next(b.source_id for b in self.sources if b.role == "primary")

    @property
    def source_ids(self) -> list[str]:
        return [b.source_id for b in self.sources]

    def binding(self, source_id: str) -> SourceBinding:
        for b in self.sources:
            if b.source_id == source_id:
                return b
        raise KeyError(
            f"Company '{self.company_id}' declares no source '{source_id}'. "
            f"Known: {self.source_ids}"
        )

    def secondary_source_ids(self) -> list[str]:
        return [b.source_id for b in self.sources if b.role != "primary"]


class ExternalIds(Strict):
    """Identifiers this company is known by outside the engine.

    `supabase_user_ids` is the seam to the NestJS tier: `server/` holds a
    `users.id` and no notion of a company at all, so until a `companies` table
    exists this is how an uploader resolves to a folder.
    """

    supabase_user_ids: list[str] = Field(default_factory=list)


class CompanyEntry(Strict):
    """One row of the registry. An index entry, not a configuration."""

    company_id: CompanySlug
    display_name: str = Field(min_length=1)
    domains: list[Domain] = Field(min_length=1)
    created_at: date
    path: str = Field(
        default="",
        description="Folder name under user/. Empty means the slug itself, which is "
        "the normal case; the field exists so a folder can be renamed without "
        "invalidating every run artefact that recorded the slug.",
    )
    active: bool = True
    external_ids: ExternalIds = Field(default_factory=ExternalIds)

    @property
    def folder(self) -> str:
        return self.path or self.company_id


class CompanyRegistry(Strict):
    """`user/metadata.yaml` -- which companies exist.

    Deliberately without a `default_company`: a company selector is required
    everywhere, and a default here would be a back door around that.
    """

    schema_version: str = SCHEMA_VERSION
    companies: list[CompanyEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids_and_folders(self) -> CompanyRegistry:
        ids = [c.company_id for c in self.companies]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"Registry repeats company ids: {dupes}")
        folders = [c.folder for c in self.companies]
        clashes = sorted({f for f in folders if folders.count(f) > 1})
        if clashes:
            raise ValueError(f"Registry maps two companies to one folder: {clashes}")
        return self

    @model_validator(mode="after")
    def _external_ids_are_not_shared(self) -> CompanyRegistry:
        """One Supabase user resolves to at most one company.

        Without this a lookup would be ambiguous exactly when it matters -- at
        upload time, deciding whose folder a file belongs in.
        """
        seen: dict[str, str] = {}
        for company in self.companies:
            for uid in company.external_ids.supabase_user_ids:
                if uid in seen:
                    raise ValueError(
                        f"supabase_user_id {uid!r} is claimed by both "
                        f"'{seen[uid]}' and '{company.company_id}'"
                    )
                seen[uid] = company.company_id
        return self

    def get(self, company_id: str) -> CompanyEntry:
        for c in self.companies:
            if c.company_id == company_id:
                return c
        raise KeyError(company_id)

    def by_external_id(self, supabase_user_id: str) -> CompanyEntry | None:
        return next(
            (
                c
                for c in self.companies
                if supabase_user_id in c.external_ids.supabase_user_ids
            ),
            None,
        )
