"""The request body, and the translation from it to an agent config.

`AskRequest` mirrors `kpi_engine.cli.ask` field for field. That is deliberate:
the API is a second front-end on one command, so a question answered one way at
the terminal must be answerable the same way over HTTP, with the same defaults
and the same validation. A field the CLI does not have is a field the two can
disagree about.

`Strict` is reused from the agent's models, so `extra="forbid"` applies here too
-- a caller who misspells `time_grain` gets a 422 rather than a silently ignored
override and a report configured differently from what they asked for.

**No field here names a filesystem path, and none should.** This body used to
carry nine (`dataset`, `scm`, `source`, `scm_source`, `contract`, `scm_contract`,
`graph`, `detection`, `personas`), each a project-root-relative string. `Strict`
guards field *names*; it never guarded their values, so on an unauthenticated API
they were an arbitrary-file-read surface. A source is now named by the id its
company declared, and nothing else can name a file over the wire.
`test_no_ask_field_names_a_path` pins that.

The company is not here either -- it is a path segment,
`POST /companies/{company}/ask`. It belongs to the route, not to the question.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from kpi_agent.models import Persona, Strict, TimeGrain
from kpi_engine.contracts.onboarding import (
    PLAN_ID_PATTERN,
    PlanConfirmation,
    PlanDecision,
)
from kpi_engine.contracts.tenancy import AgentDefaults, CompanySlug, Domain
from kpi_engine.tenancy import CompanyPaths, company_config


class AskRequest(Strict):
    question: str = Field(description="What you want to know, in plain language.")

    persona: Persona | None = Field(
        default=None,
        description="Who is asking. Inferred by the planner when omitted.",
    )

    # The planner is not deterministic, so a demo that depends on it choosing the
    # right grain is a demo that sometimes fails. These two force the fields that
    # decide whether the detector finds anything at all.
    #
    # `None` and `[]` are different instructions and must survive the JSON round
    # trip as different: `None` means the caller said nothing and the planner
    # decides; `[]` means total level and is a real choice.
    time_grain: TimeGrain | None = Field(
        default=None,
        description="Force the grain over whatever the planner proposes. Coarse "
        "grains starve the baseline: the detector trains on a fixed number of "
        "periods, so at month grain over a two-year file it never trains at all.",
    )
    entity_keys: list[str] | None = Field(
        default=None,
        description="Force the slicing dimensions (e.g. ['Region'], ['Supplier']). "
        "An empty list means total level; null means the planner decides.",
    )

    model: str | None = Field(default=None, description="Override the model id.")
    no_llm: bool = Field(
        default=False,
        description="Run the deterministic path only: a broad sweep and a "
        "template-rendered report. Needs no API key.",
    )

    top_events: int = Field(default=5, ge=1, le=50)
    run_id: str | None = Field(
        default=None,
        # Constrained rather than merely checked on read: this becomes a directory
        # name, and refusing a bad one at POST is better than 400ing the later GET.
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
        description="Names the directory under the company's outputs/.",
    )

    sources: list[str] | None = Field(
        default=None,
        description="Restrict the run to these declared source ids. Omit to use "
        "every source the company declares.",
    )

    logs: bool = Field(
        default=True,
        description="Forward the run's stage log as `log` events -- the same "
        "commentary the terminal prints while a run is happening.",
    )

    # Deliberately absent: any date field. A period reaches the pipeline only
    # through the planner's `AnalysisIntent.date_start`/`date_end`, which
    # `run_pipelines` turns into a `report_window`. Exposing dates here is the
    # easiest way to have them arrive as a `date_range` instead, which starves
    # the baseline and reports a quiet quarter that is really a short one.


def build_config(paths: CompanyPaths, req: AskRequest) -> dict[str, Any]:
    """`AskRequest` + a company -> the config dict `stream_agent` takes.

    Same assembly as `cli/ask.py`, so the two front-ends cannot drift.
    """
    config = dict(company_config(paths))
    if req.sources is not None:
        unknown = [s for s in req.sources if s not in paths.spec.source_ids]
        if unknown:
            raise ValueError(
                f"Company '{paths.slug}' declares no source(s) {unknown}. "
                f"Known: {paths.spec.source_ids}"
            )
        config["sources"] = list(req.sources)

    config["top_events"] = req.top_events
    # Assigned unconditionally, including `None`: the graph reads these as
    # "overrides" and `None` there means the planner was not overruled.
    config["time_grain"] = req.time_grain
    config["entity_keys"] = req.entity_keys
    return config


class CreateCompanyRequest(Strict):
    """`POST /companies` -- provision a tenant.

    Deliberately bare bones: it carries no KPI fields, and still should not. A
    company created here gets its template's KPIs; a company whose extract does
    not match any template gets them from the onboarding handshake instead --
    `POST /companies/{c}/kpi-plan`, then `.../kpi-plan/confirm`, which rewrites
    `configs/semantics/` and `configs/causal/` from the file itself. Either way
    exactly one thing decides what a company measures, and it is never this body.

    Seed such a tenant from the `blank` template: it declares no KPIs, so the
    company never briefly claims metrics it cannot compute.

    A company is created *before* it has data. Its sources come from the template
    and point at files that do not exist yet, so it reports `awaiting_data` until
    a CSV is attached at `POST /companies/{company}/sources/{source_id}/data`, or
    until an onboarding plan is confirmed.
    """

    company_id: CompanySlug
    display_name: str = Field(min_length=1)
    domains: list[Domain] = Field(min_length=1)
    template: str | None = Field(
        default=None,
        description="Which templates/company/<name>/ to seed from. Defaults to the "
        "first domain that has a template, else `minimal`.",
    )
    agent: AgentDefaults | None = Field(
        default=None, description="Override the template's time_grain/entity_keys/top_events."
    )
    supabase_user_ids: list[str] = Field(
        default_factory=list,
        description="Supabase `users.id` values that resolve to this company. The "
        "registry refuses an id already claimed by another tenant.",
    )
    notes: str = ""


class PlanKpisRequest(Strict):
    """`POST /companies/{c}/kpi-plan` -- propose a KPI configuration from a CSV.

    The file arrives as multipart; these are the knobs beside it. As with
    `AskRequest`, **no field names a filesystem path**: the upload is a body part
    and the source is named by the id its company declared.
    """

    source_id: str | None = Field(
        default=None,
        description="Which declared source this file is. Defaults to the primary.",
    )
    model: str | None = Field(default=None, description="Override the model id.")
    no_llm: bool = Field(
        default=False,
        description="Bind by exact column-name matching only. Needs no API key, "
        "and proposes fewer KPIs -- never wrong ones.",
    )
    plan_id: str | None = Field(
        default=None,
        pattern=PLAN_ID_PATTERN,
        description="Name the draft. Generated from the clock when omitted.",
    )
    logs: bool = Field(default=True, description="Forward the stage log as `log` events.")


class ConfirmPlanRequest(Strict):
    """`POST /companies/{c}/kpi-plan/confirm` -- accept, edit, and commit a draft.

    Mirrors `PlanConfirmation` field for field, plus the transport knobs -- the
    same relationship `AskRequest` has to the `ask` CLI, so the terminal and the
    wire cannot configure a company differently.

    An empty `decisions` list is not "accept nothing": it means the caller had no
    corrections, and the plan's own recommendations stand. Rejecting everything
    requires saying so, because a company with no KPIs answers every question
    with silence.
    """

    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    decisions: list[PlanDecision] = Field(default_factory=list)
    date_column: str | None = None
    entity_columns: list[str] | None = Field(
        default=None,
        description="null keeps the plan's choice; [] means total level and is a "
        "real instruction, not an omission.",
    )
    time_grain: TimeGrain | None = None
    contract_id: str | None = None
    warm_up: bool = Field(
        default=True,
        description="Run the pipeline once after writing, so the first question "
        "does not pay for it.",
    )
    model: str | None = None
    no_llm: bool = Field(
        default=False,
        description="Skip the causal-structure call. The DAG keeps its derived "
        "deterministic edges and the catalogue's lever ownership.",
    )
    run_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
        description="Names the warm-up's directory under the company's outputs/.",
    )
    logs: bool = True

    def as_confirmation(self) -> PlanConfirmation:
        return PlanConfirmation(
            plan_id=self.plan_id,
            decisions=list(self.decisions),
            date_column=self.date_column,
            entity_columns=self.entity_columns,
            time_grain=self.time_grain,
            contract_id=self.contract_id,
            warm_up=self.warm_up,
        )
