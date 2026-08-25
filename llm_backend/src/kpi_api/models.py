"""The request body, and the translation from it to an agent config.

`AskRequest` mirrors `kpi_engine.cli.ask` field for field. That is deliberate:
the API is a second front-end on one command, so a question answered one way at
the terminal must be answerable the same way over HTTP, with the same defaults
and the same validation. A field the CLI does not have is a field the two can
disagree about.

`Strict` is reused from the agent's models, so `extra="forbid"` applies here too
-- a caller who misspells `time_grain` gets a 422 rather than a silently ignored
override and a report configured differently from what they asked for.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from kpi_agent.graph import DEFAULT_CONFIG
from kpi_agent.models import Persona, Strict, TimeGrain


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
        default=None, description="Names the directory under outputs/."
    )

    # Config and dataset overrides, one per CLI flag.
    dataset: str | None = None
    scm: str | None = None
    source: str | None = None
    scm_source: str | None = None
    contract: str | None = None
    scm_contract: str | None = None
    graph: str | None = None
    detection: str | None = None
    personas: str | None = None

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


def build_config(req: AskRequest) -> dict[str, Any]:
    """`AskRequest` -> the config dict `stream_agent` takes.

    Same assembly as `cli/ask.py`, so the two front-ends cannot drift on how an
    override is spelled or on which entry of `sources` a dataset belongs to.
    """
    config = dict(DEFAULT_CONFIG)
    sources = [dict(s) for s in config["sources"]]
    if req.source:
        sources[0]["source"] = req.source
    if req.contract:
        sources[0]["contract"] = req.contract
    if req.dataset:
        sources[0]["dataset"] = req.dataset
    if req.scm_source:
        sources[1]["source"] = req.scm_source
    if req.scm_contract:
        sources[1]["contract"] = req.scm_contract
    if req.scm:
        sources[1]["dataset"] = req.scm
    config["sources"] = sources

    for key, value in (("graph", req.graph), ("detection", req.detection),
                       ("personas", req.personas)):
        if value:
            config[key] = value

    config["top_events"] = req.top_events
    # Assigned unconditionally, including `None`: the graph reads these as
    # "overrides" and `None` there means the planner was not overruled.
    config["time_grain"] = req.time_grain
    config["entity_keys"] = req.entity_keys
    return config
