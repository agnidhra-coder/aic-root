# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Two packages with a hard boundary between them.

`src/kpi_engine/` is the deterministic core: it detects material KPI movements,
attributes them to drivers, and emits structured evidence. **No LLM anywhere in
`kpi_engine`** — that is deliberate, not an omission, and it must stay true.

`src/kpi_agent/` is the LangGraph layer on top. It calls a model exactly twice —
once to turn a plain-language question into a validated pipeline configuration,
once to write prose — and never to produce a quantity. Between those two calls
everything is `kpi_engine`, untouched.

See `README.md` for the design rationale and measured results, `baseline_plan.md`
for the original spec, `problem_statement.md` for the hackathon acceptance
criteria.

## Commands

```bash
uv sync --extra dev --extra agent --extra cli --extra api   # setup (CPython 3.11, editable install)
# `uv sync` is exact -- it uninstalls every extra you do not name. Naming a
# subset later (`uv sync --extra api`) is what silently removes rich and pytest.
uv run pytest tests/ -q                          # 216 tests
uv run pytest tests/test_causal.py::test_shapley_efficiency_axiom -q   # single test

# every command names a company. `acme-retail` is the demo tenant.
export C=acme-retail
uv run python -m kpi_engine.cli.list_companies             # who exists, and are they ready
uv run python -m kpi_engine.cli.init_company --company $C --check  # validate; writes nothing

uv run python -m kpi_engine.cli.profile_source   --company $C  # stage 0: schema, redundancy, coverage
uv run python -m kpi_engine.cli.inject_scenario  --company $C  # stage 0b: ground-truth events
uv run python -m kpi_engine.cli.compute_kpis     --company $C  # stage 1: KPI panel
uv run python -m kpi_engine.cli.profile_series   --company $C  # stage 1b: trend/seasonality/quality
uv run python -m kpi_engine.cli.detect_anomalies --company $C  # stage 2: flags -> event windows
uv run python -m kpi_engine.cli.explain_event    --company $C  # stage 3: attribution + evidence
uv run python -m kpi_engine.cli.evaluate         --company $C  # stage 4: score vs ground truth
uv run python -m kpi_engine.cli.generate_scm     --company $C  # stage 0c: derive the weekly SCM source
uv run python -m kpi_engine.cli.export_schemas                 # product-level; no --company

# create a tenant (its KPIs come from the template; see "Adding things")
uv run python -m kpi_engine.cli.init_company --company orbit-grocers \
    --display-name "Orbit Grocers" --domain other --template minimal --from-csv extract.csv

# ask a question (two model calls; key read from .env)
uv run python -m kpi_engine.cli.ask --company $C "why did margin fall in the West?" --persona ops
uv run python -m kpi_engine.cli.ask --company $C "why did ROAS drop?" --model gemini-3.7-flash
uv run python -m kpi_engine.cli.ask --company $C "what needs attention?" --persona exec --no-llm
# force the configuration rather than trusting the planner (see README for the two demo questions)
uv run python -m kpi_engine.cli.ask --company $C "..." --persona analyst \
    --time-grain week --entity-keys Supplier

# the same thing over HTTP, one typed event per stage as it lands
uv run python -m kpi_api                         # 127.0.0.1:8000, /docs for the schema
curl localhost:8000/companies
curl -N -X POST localhost:8000/companies/$C/ask -H 'content-type: application/json' \
  -d '{"question":"what needs attention?","no_llm":true,"persona":"exec"}'

# provision over HTTP, then give it data
curl -X POST localhost:8000/companies -H 'content-type: application/json' \
  -d '{"company_id":"demo-co","display_name":"Demo Co","domains":["retail"]}'
curl -F file=@extract.csv localhost:8000/companies/demo-co/sources/retail_daily/data

# end to end
uv run python -m kpi_engine.cli.run_pipeline --company $C \
    --entity-keys Region --time-grain week --evaluate
```

Common flags: `--company` (**required** on every command but `export_schemas`),
`--source-id` (which declared source; defaults to the company's primary),
`--entity-keys`, `--time-grain day|week|month`, `--kpis`, `--run-id`. Artefacts
land in `user/<company>/outputs/<run_id>/`.

## Tenancy

Everything a company owns lives under one folder. There is no shared `configs/`
or `data/` any more, and no default company: a command that ran against whichever
tenant happened to be configured would report one company's numbers under
another's name.

```
user/
  metadata.yaml              # CompanyRegistry: which tenants exist
  acme-retail/               # the demo tenant, and what the thresholds were calibrated on
    company.yaml             # CompanySpec: sources, config paths, agent defaults
    configs/{agent,causal,detection,eda,scenarios,semantics,sources}/
    data/{raw,generated,profiles}/
    outputs/<run_id>/
  orbit-grocers/             # one source, a different contract
  testco/                    # pytest fixture; declares `retail_daily` like acme does
templates/
  company/{retail,supply-chain,minimal}/   # seed material; each is itself a CompanySpec
  reference/{kpi_list.csv,KPI_doc.xlsx}    # what an author consults writing a contract
schemas/                     # product-level, generated from the Pydantic models
```

- `src/kpi_engine/contracts/tenancy.py` — `CompanySpec`, `CompanyRegistry`,
  `SourceBinding`. `CompanySlug` is validated here, which is what makes traversal
  through a company selector impossible on the CLI and the API at once.
- `src/kpi_engine/tenancy.py` — `CompanyPaths`: the **only** module that turns a
  config string into a filesystem path, plus the typed, memoised loaders.
- `src/kpi_engine/provisioning.py` — `create_company` and `attach_source_data`.
  `cli.init_company` and `POST /companies` are both thin wrappers over them.

`$KPI_USER_ROOT` relocates `user/` for a deployed install; `$KPI_OUTPUTS_ROOT`
relocates every company's `outputs/` (the test suite points it at a tmp dir).

## Architecture

Stages chain through `user/<company>/outputs/<run_id>/`, each runnable alone:
`profile → panel → [series profile] → detect → attribute → evaluate`.
See `RUNBOOK.md` for what each stage reads, writes, and what to look for.

- `contracts/` — Pydantic models for every config and payload. Nothing in the
  engine reads a raw dict; an invalid config fails before any computation.
- `tenancy.py` / `provisioning.py` — see **Tenancy** above. `CompanyPaths` is the
  one seam between a config string and a path; `run_pipeline` takes it as a
  required argument, so a run cannot be started without saying whose it is.
- `sources/` — `DataSource` protocol + csv/excel adapters via `registry.py`.
  Adding a backend is one class plus one registry line.
- `profiling/` — duplicate columns, exact linear identities, grain coverage.
  Output gates which columns may act as causal drivers.
- `semantics/` — restricted AST evaluator (never `eval`) and the panel builder.
- `eda/` — descriptive series profiling: Theil-Sen trend, seasonality, PELT
  segments, support/coverage. Emits no `Flag`; nothing downstream branches on
  it, which is what keeps it unable to regress detection.
- `detection/` — STL → expanding baseline → {point, changepoint, multivariate} →
  windowing.
- `causal/` — `algebraic` (Layer A, exact) and `router` dispatching DiD/ITS/DML
  (Layer B, estimated) over the governed DAG.
- `evidence/` — confidence, abstention, `EvidenceBundle`, telemetry.
- `pipeline.py` — the whole chain in one process, returning objects rather than
  argv-chaining the CLIs. What `kpi_agent` calls. Takes a required `paths:
  CompanyPaths`. Verified against the CLI chain by
  `test_in_process_pipeline_reproduces_the_cli_chain`.

`src/kpi_agent/` — the LangGraph layer:
`ingest → plan → validate → run → link → facts → narrate → verify → report`.
- `catalog.py` — metadata-only view of the sources; what the planner may see. A
  pure function over already-loaded objects: it touches the filesystem zero times,
  so tenancy never reaches it.
- `intent.py` — LLM call #1, plus the deterministic validator that resolves or
  rejects what it proposed.
- `linking.py` — cross-source event alignment, licensed by the DAG.
- `facts.py` — flattens bundles into a numbered fact table; the narrator's
  entire context.
- `narrate.py` — LLM call #2, plus the template fallback.
- `llm.py` — the model's configuration, `build_llm`, token accounting, and the
  wording of a failed call. It holds no call site: `intent.py` and `narrate.py`
  call `.with_structured_output(...).invoke(...)` on the model object themselves.
- `verify.py` — deterministic grounding check. No model involved.
- `graph.py` — `stream_agent` yields `(node, state-so-far)` as each node
  completes; `run_agent` is a drain of it. One execution path, two ways of
  watching it. Both take a required `company`; there is no module-level default
  config left to fall back on.

`src/kpi_api/` — HTTP, and nothing else:
- `events.py` — the projection from `AgentState` to typed events, and the only
  place anything is serialised. Every event names its fields.
- `logbus.py` — the stage log forwarded per-run, routed by a `ContextVar` so two
  concurrent runs cannot land in each other's stream.
- `app.py` — routes, CORS, the worker-thread-to-queue bridge, the concurrency
  guard, and the company dependency every scoped route depends on.
  `models.py` mirrors the `ask` flags and adds `CreateCompanyRequest`;
  `__main__.py` starts uvicorn.

Routes: `GET /health`, `GET|POST /companies`, `GET /companies/{c}`,
`POST /companies/{c}/sources/{sid}/data`, `POST /companies/{c}/ask[/sync]`,
`GET /companies/{c}/runs/{run_id}[/report]`.

## The wider system

`llm_backend/` is one of three tiers in this repo
(`github.com/agnidhra-coder/aic-root`), and **only two of them are wired
together**:

```
frontend/    Next.js 16, :3000   ──fetch──▶   server/   NestJS + Supabase, :3001  ──▶ Supabase
llm_backend/ FastAPI,     :8000  ◀── nothing in this repo calls this yet
```

The frontend has exactly one backend: `NEXT_PUBLIC_API_URL`, default `:3001`
(`frontend/lib/api.ts:1`). There is no `app/api/` directory, no `EventSource`,
and no reference to port 8000 anywhere in `frontend/` or `server/`. **Every call
into Python is meant to arrive from NestJS, server to server** — which is why
`kpi_api` has no authentication (`kpi_api/__main__.py` binds `127.0.0.1` for
exactly this reason), why `GET /companies` may list every tenant, and why
`POST /companies` — which writes to disk — must never be reachable from a browser.

**The integration point is one function.** `server/src/analysis/README.md` is the
spec: replace `runSimulatedAnalysis` (`server/src/analysis/simulated-analysis.ts`),
called from exactly one site, `server/src/analysis/analysis.service.ts`, and
return an `AnalysisResult`. That type is defined in
`server/src/analysis/analysis.entity.ts` and mirrored verbatim in
`frontend/lib/api.ts` — edit both together or the frontend renders garbage. The
four stages the service steps through (`detect`, `decompose`, `explain`, `act`,
`STAGE_DELAY_MS = 1250`) are a timed fake; the frontend already polls `stage`
live, so mapping them onto real agent nodes needs no frontend change. Note the
README is slightly stale: `KpiCase` has since gained
`driverBreakdown: DriverKpi[]`, which it does not mention.

**A Supabase `user_id` maps to a company through the registry.**
`server/supabase/schema.sql` has `users / uploads / analyses` and **no company or
tenant entity at all**; scoping is application-side (`.eq('user_id', userId)`)
with no RLS. Until a `companies` table exists, the mapping lives in
`user/metadata.yaml` as `external_ids.supabase_user_ids`, is exposed by
`GET /companies`, and is set at creation by `POST /companies`. The intended flow:

1. On a user's first upload, `POST /companies` with their `users.id` in
   `supabase_user_ids`.
2. Forward the CSV buffer NestJS already holds — it never re-reads from storage —
   to `POST /companies/{slug}/sources/{source_id}/data`.
3. `POST /companies/{slug}/ask`.

Uploads land in the Supabase bucket `kpi-uploads` under
`${userId}/${Date.now()}-${filename}`, so the local mirror of that is
`user/<slug>/data/raw/`. The domain contracts already agree:
`server/src/uploads/kpi-contracts.ts` declares retail dimensions `Region,
Channel, Product category, Traffic source` — exactly what the retail source
config carries — and supply-chain `Region, DC, Lane, Carrier, Supplier, SKU
category`, a superset of the SCM config's.

**Event → `AnalysisResult`**, for whoever writes the adapter:

| SSE event (`kpi_api/events.py`) | `AnalysisResult` field |
|---|---|
| `ingest` | `rowCount`, `columns` — primary source only |
| `plan` | nothing user-facing; the audit trail |
| `engine` | one `KpiCase` per `EventWindow`; `segment` from `EventWindow.entity` |
| `evidence` | the bulk: `movement` facts → `detect.headline`/`value`/`delta`; `method`/`confidence`/`quality` → `detect.statSignificance`; `Contribution` rows → `EvidenceItem[]`, with `exact` distinguishing algebra from estimate; `links` → `EvidenceItem{kind:"exogenous"}`; `abstentions` → `tier: "UNEXPLAINED"` |
| `EvidenceBundle.confidence` | `contributionTotal`, hence `tier`. The README's ~70% bar **is** `DetectionSpec.confidence_threshold` (default `0.70`) — keep them equal deliberately, not by coincidence |
| `narrative` | `narratives.operational` / `.strategic`, from the `ops` and `exec` personas — which means **two narrate calls or two runs**, and nothing does that today |
| `lever` facts, `Narrative.actions` | `ActionPlan{driver, lever, action, impact, owner, confidence, monitor}` |
| `verification`, `report` | **nothing** — `AnalysisResult` has no slot for the grounding check or the markdown |

**Still missing, plainly:** no adapter, in either language; no SSE client in
`server/` (its `run()` is synchronous-shaped, so `/ask/sync` is the shortcut if
streaming is deferred); no company entity in the SQL schema; and `AnalysisResult`
has no field for verification, abstention rationale, or the report — the three
things this engine produces that the simulated one cannot. Extending the
TypeScript interface is part of the integration, not an afterthought.

## Invariants — do not break these

- **Ratio-of-sums, never mean-of-ratios.** Aggregate measures to the grain first,
  then apply the formula. Tested against the alternative in `test_semantics.py`.
- **No lookahead.** Baselines train strictly on data before `t`.
  `test_baseline_uses_no_future_information` fails if this regresses.
- **Formulas are parsed, not evaluated.** Extend the whitelist in
  `semantics/expressions.py` deliberately; formula strings are untrusted input.
- **Detection stays independent of attribution.** Stage 2 decides *that*
  something moved, stage 3 decides *why*. Do not let one reach into the other.
- **Collinear columns never become independent drivers.** Consult the profile's
  `redundant_columns`. An identity over *k* columns drops exactly one, not *k*.
- **A requested period is a report window, not a load filter.** `pipeline.run_pipeline`
  takes both: `date_range` restricts what is read (and therefore starves the
  baseline), `report_window` detects over the full history and filters events at
  the end. The agent must use `report_window`. Using `date_range` for "what
  happened last quarter" gives the detector 3 periods against a 28-period history
  requirement; it then finds nothing and reports a quiet quarter, which is a wrong
  answer that looks like a right one.
- **The DAG is declared, not discovered.** Add edges to the company's
  `configs/causal/*.yaml` with direction from domain knowledge; the
  `forbidden_edges` list is checked at load.
- **Exact and estimated stay labelled.** `Contribution.exact` distinguishes
  algebra from estimation. Never present an estimate as exact.
- **The EDA stage describes; it never decides.** `eda/` emits no `Flag` and no
  stage reads its output. Keep it that way — its independence is what lets it be
  changed freely without re-verifying detection.
- **Robust estimators over least squares in `eda/`.** Theil-Sen for slopes,
  MAD for scale. OLS slope and std are dragged by exactly the outliers stage 2
  exists to flag, so a single spike would be reported as a trend.
  `test_slope_is_robust_to_a_single_spike` pins this.
- **Thresholds are calibrated, not guessed.** After touching a company's
  `configs/detection/default.yaml`, re-run `cli.evaluate --company <slug>` and
  check recall did not fall. Baseline on `acme-retail`, which is what the numbers
  were measured against: recall 100%, precision 54.5%, F1 0.706. Another tenant's
  thresholds are its own; do not copy one company's calibration onto another and
  assume it holds.
- **Detection's STL period is not grain-aware, and that is on purpose.** `eda/`
  makes its decomposition period grain-aware; detection deliberately does not.
  Setting `period=4` at weekly grain was tried and measured: flags 96 → 73,
  events 11 → 8, recall 100% → 83.3%. A 4-period cycle at weekly grain lets STL
  absorb a month-long shock as seasonality. The reasoning is recorded at the top
  of `pipeline.py`; do not "fix" it back.
- **Grain is bounded below by noise and above by history.** `baseline.min_train_periods`
  counts *periods*, not days, so at month grain over this two-year file the index
  never reaches 56, no expectation is emitted, and the detector finds nothing while
  reporting a quiet quarter. `intent.validate_intent` steps a starved grain down to
  the finest one that clears the bar and records the step in `problems`;
  `_resolve_grain` holds the arithmetic. `test_a_grain_the_baseline_could_never_train_on_is_lowered`
  pins it. Do not "simplify" this away by trusting the planner: the planner is what
  chose month.
- **A CLI override outranks the planner, and is still validated.** `--time-grain`
  and `--entity-keys` arrive as `overrides` and replace what the model proposed
  before every other check runs, so a demo does not depend on the planner's mood.
  An override that the data cannot support gets a warning, never a silent
  substitution. `None` means "the caller said nothing"; `[]` means "total level"
  and is a real instruction.
- **`render_markdown` is the artefact; `render_console` is a view.** `agent_report.md`
  is what makes a report checkable, so the markdown renderer must keep producing
  exactly what it produced before. The terminal renderer is a second presentation
  of the same objects and never a second source of truth --
  `test_the_console_view_does_not_replace_the_markdown_artefact` pins that, and
  `ask --plain` is byte-identical to the written file.
- **The API is a third view, not a second engine.** `run_agent`, `POST /ask` and
  `POST /ask/sync` all drain `stream_agent`, and `/ask/sync` is built by folding
  the same events `/ask` streams. An endpoint that assembles its own graph run,
  or that serialises `AgentState` wholesale, gives up the guarantee that the
  terminal and the wire report the same numbers for the same question --
  `test_the_sync_endpoint_and_the_stream_report_the_same_thing` and
  `test_streaming_the_graph_reproduces_what_invoke_returns` pin both halves. The
  equivalence rests on `AgentState` declaring no reducers, so adding an
  `Annotated[list, add]` field breaks it; the second test is what says so.
- **`AgentState` never reaches the wire.** It carries a live model, a `Usage`,
  raw DataFrames and `PipelineResult` dataclasses. `events.py` projects field by
  field and `_sse` encodes with no `default=` fallback, so a leak fails loudly
  rather than shipping `"<object at 0x...>"` to a client.
- **`rich` is presentation, never capability.** It lives in the `cli` extra and is
  imported lazily behind `_common.console()`. Every path has a plain-text answer;
  a missing dependency costs colour and box-drawing, never a line of output and
  never a computed number.
- **A path is company-relative or absolute; never project-relative.**
  `tenancy.CompanyPaths` is the only module that turns a config string into a
  filesystem path. `project_root()` locates `user/`, `templates/`, `schemas/` and
  `.env` -- nothing else. `test_project_root_is_not_a_config_or_data_anchor` and
  `test_only_tenancy_knows_where_the_package_sits_on_disk` pin both halves, so
  reintroducing a project-relative config path fails rather than drifting.
- **A company selector is required, everywhere, with no default.** No ambient env
  var, no `default_company` in the registry, no module-level config to fall back
  on. A run configured from whichever tenant happened to be wired in reports one
  company's numbers under another's name -- which is precisely the class of wrong
  answer the rest of these invariants exist to prevent.
- **A missing config fails loudly; there is no run-time fallback to `templates/`.**
  A silent fallback means two tenants share a DAG, and `verify.py` would then
  check company A's narrative against company B's causal graph.
- **Derived state about a company is derived, never stored.** `data_ready` is
  "every declared dataset exists on disk", computed each time. A `status:` field
  in `company.yaml` would drift from the filesystem, and the first thing it would
  mislead is whether an answer can be trusted.
- **The profile cache is per company, and lives under `data/`, not `outputs/`.**
  Two tenants may legitimately name a source `retail_daily`; the old global cache
  was keyed on that name alone. It sits beside the data rather than the run
  artefacts because it describes a file, and because building one searches signed
  combinations of up to three columns over ~38 columns -- minutes, not seconds, so
  it must survive an `$KPI_OUTPUTS_ROOT` redirect.
  `test_two_companies_sharing_a_source_id_get_different_profile_caches` pins it.
- **No `AskRequest` field may name a filesystem path.** It once carried nine
  project-root-relative path strings; `extra="forbid"` guards field *names*, never
  their values, so on an unauthenticated API they were an arbitrary file read. A
  source is named by its declared id. `test_no_ask_field_names_a_path` pins it.
- **A CSV is validated against the contract before it is accepted.** Every column
  a KPI's measures name, plus the date and entity columns, must be in the header
  or `attach_source_data` refuses the file. Accepting it instead yields an empty
  panel and a report that nothing happened.
- **The LLM never produces a number.** It chooses the configuration and writes the
  prose. Every quantity in a report comes from an `EvidenceBundle` by way of the
  fact table in `kpi_agent/facts.py`.
- **One model, configured in one place.** `llm.DEFAULT_MODEL` (`gemini-3.5-flash-lite`)
  is the only place a model name is written; `TIMEOUT_SECONDS`, `ATTEMPTS`,
  `MAX_OUTPUT_TOKENS` and `REASONING_EFFORT` sit beside it, and `build_llm` is the
  only constructor. `--model` overrides the name for a run. Do not scatter a second
  default into a node, a config dict, or a CLI default.
- **The nodes call LangChain directly; nothing wraps the model.** `run_agent` puts
  one `ChatGoogleGenerativeAI` and one `Usage` in the run config, and the two nodes
  that need a model reach for them there. There is no client class and no `parse`
  indirection to keep in step with LangChain's API. `llm=None` is the `--no-llm`
  path: both nodes check for it and take the deterministic branch.
- **Structured output is `method="json_schema"`.** It sends the Pydantic model's own
  `model_json_schema()` as `response_json_schema`, so `$ref`, `anyOf` and the
  `additionalProperties: false` that `extra="forbid"` emits all survive the trip —
  the constraint the API enforces is the strict schema we wrote, and the reply is
  validated against that same model coming back. Older Gemini schema dialects
  rejected all three and needed a hand-written translator; do not bring one back,
  and do not relax the Pydantic models to please an API.
- **Build the model once; never `model_copy` it.** The copy shares the underlying
  httpx client and closes it when collected, so the *second* call of a run dies with
  "Cannot send a request, as the client has been closed". Both calls therefore take
  the same effort and output cap, which is what makes one shared object correct.
  `test_the_model_is_configured_in_exactly_one_place` pins the settings.
- **Model availability is not a constant.** `gemini-3.7-flash` has been observed
  returning `503 "high demand"` for minutes on end while `gemini-3.5-flash-lite`
  (the default) answered the identical request in seconds. Before concluding the agent is
  broken, try another `--model`. A saturated model is not a defect in the request,
  and `llm.unavailable` says so in the message.
- **An unpriced model reports no cost, not zero.** `Usage.cost_usd` is `None` when
  rates are unknown and `RunManifest.llm_cost_usd` is nullable. Zero would read as
  a free run.
- **`kpi_agent/verify.py` is the enforcement, not the system prompt.** The prompt
  asks the narrator to quote only from the fact table; the verifier checks that it
  did, mechanically, with no second model adjudicating the first. Two failures drop
  to the template. Weakening the verifier removes the guarantee entirely.
- **A cross-source link needs a declared DAG edge.** Overlapping windows are not a
  link. `test_no_declared_path_means_no_link` pins this: same region, identical
  windows, no path, no link.
- **The fallback must always pass its own verifier.** It is the floor under the
  whole design. Personas select different fact kinds, so test every persona —
  `test_the_template_narrative_passes_its_own_verifier_for_every_persona`.

## Data facts worth knowing

All paths below are under `user/acme-retail/`.

- `data/raw/dummy_data.csv` is **statistically flat** — no trend, seasonality, or
  autocorrelation. Detections on it are noise by construction. The primary source
  binding's `dataset:` override therefore points `retail_daily` at
  `data/generated/ad_cost_shock_v1.csv`, which is what anything needing real
  signal reads.
- Exact redundancies: `Net Sales == Total Revenue == Retail Sales`,
  `Number of Sales == Total Transactions`, `Total Gross Profit == Net Sales − COGS`,
  `End Customers == Start + New − Lost`.
- Profiling a source is **slow, in the column count**: `find_linear_identities`
  searches signed combinations of up to three columns, so a 38-column extract
  takes minutes. That is why the cache under `data/profiles/` is committed and why
  `attach_source_data` builds it up front rather than at first question.
- `data/generated/scm_weekly_v1.csv` is **derived from** the sales file, not
  independent of it: `Units Received` is its `Units Sold` aggregated to
  region × category × week, `Purchase Price Per Unit` its `COGS` per unit.
  Regenerate with `cli.generate_scm` after changing the sales data, or the two
  files will contradict each other. Weekly grain and a `Supplier` dimension the
  sales file does not carry — the mismatch is the point.
- The panel is **sampled, not dense**: ~27 rows/day total, ~5.5 per region-day,
  ~1.2 per Region×Channel×Category day. Grain must match signal-to-noise —
  region-level daily ratios are too noisy to model, which is why the demos run at
  weekly grain. The deepest grain is the abstention scenario.

## Adding things

- **A company**: `python -m kpi_engine.cli.init_company --company <slug>
  --display-name "…" --domain retail --from-csv <path>`, or `POST /companies` for
  the same thing over HTTP — both call `provisioning.create_company`. Never
  `mkdir` one by hand: `company.yaml` is rendered from the `CompanySpec` that
  reads it, and a hand-made folder skips the header validation that catches a CSV
  missing a KPI's measure. A new company's KPIs are whatever its template
  declares; choosing them per company is separate, later work.
- **A template**: add `templates/company/<name>/` with a `company.yaml` and a
  `configs/` tree. The `company.yaml` is itself a real `CompanySpec` — not a
  string template with placeholders — so a template that stops parsing fails at
  `init_company --check` rather than at some tenant's first question.
- **A KPI**: add to the company's `configs/semantics/*.yaml` (measures +
  expression + materiality + guards), then add its nodes and deterministic edges
  to that company's DAG. Add it to `templates/` too if every new tenant should
  get it. `templates/reference/kpi_list.csv` is the catalogue of formulas and
  drivers to write it from.
- **A detector**: implement it in `detection/`, return `list[Flag]`, call it from
  `detection/runner.py`. Windowing and everything downstream is additive.
- **A causal method**: add the estimator, then a branch in `causal/router.py` that
  states its preconditions and populates `MethodChoice.considered` on rejection.
- **A data source**: subclass `DataSource`, `register_source(...)`, extend the
  `SourceSpec.type` literal.
- **A persona**: add it to the company's `configs/agent/personas.yaml` (`include_facts`,
  `max_facts`, `restricted_entitlements`, `brief`), extend the `Persona` literal in
  `kpi_agent/models.py`, and add it to the parametrised template test. Entitlement
  is enforced by dropping facts in `facts.py`, never by asking the narrator to be
  discreet.
- **A verifier rule**: add a `Violation` code in `models.py` and the check in
  `verify.py`. The repair message is what the narrator sees on retry, so it must
  say what to do, not just what is wrong.

## Note on pandas

pandas 3.0 is installed and no longer silently upcasts integer columns on
assignment. `scenarios/injector.py::_assign` handles this explicitly — integer
columns here are physical counts and are rounded rather than widened to float.
