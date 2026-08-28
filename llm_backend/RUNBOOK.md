# Runbook

Commands and flags only. For how the pipeline works, see the architecture
reference.

---

## Setup

```bash
uv sync --extra dev              # deterministic engine only
uv sync --extra dev --extra agent   # + LangGraph agent (needed for `ask`)
```

Create a `.env` file at the project root for the agent layer:

```
GOOGLE_API_KEY=your-key-here
```

`.env` is gitignored and loaded automatically. No key is needed for
`--no-llm` or for any `kpi_engine.cli.*` command.

Artefacts land in `user/<company>/outputs/<run_id>/`. Pass `--run-id` to make
several stages share one directory, or omit it to get a timestamped one.

---

## Companies

Every command below takes a **required** `--company`. There is no default: a
command that ran against whichever tenant happened to be configured would report
one company's numbers under another's name.

```bash
uv run python -m kpi_engine.cli.list_companies            # who exists, and are they ready
uv run python -m kpi_engine.cli.list_companies --json     # same shape as GET /companies
uv run python -m kpi_engine.cli.init_company --company acme-retail --check  # validate; writes nothing

uv run python -m kpi_engine.cli.init_company --company orbit-grocers \
    --display-name "Orbit Grocers" --domain other --template minimal \
    --from-csv extract.csv
```
| flag | default | description |
|---|---|---|
| `--company` | — | Slug: `^[a-z0-9][a-z0-9_-]{1,62}$`. Required. |
| `--display-name` | the slug | Human-readable name. |
| `--domain` | `other` | Repeatable. |
| `--template` | first domain with a template | Which `templates/company/<name>/` to seed from. |
| `--from-csv` | none | CSV to attach after creating. Omit and the company is `awaiting_data`. |
| `--source-id` | the primary | Which declared source `--from-csv` belongs to. |
| `--supabase-user-id` | none | Repeatable. Recorded so an upload resolves to this folder. |
| `--force` | off | Overwrite an existing folder; accept a CSV missing contract columns. |
| `--check` | off | Validate an existing company and exit. Exit 2 if it has problems. |
| `--json` | off | Machine-readable result. |

Seeded tenants: `acme-retail` (the demo, and what the thresholds were calibrated
on), `orbit-grocers` (one source, a smaller contract), `testco` (pytest fixture;
declares `retail_daily` exactly as acme does, so anything sharing state by source
id alone fails loudly).

Templates: `retail`, `supply-chain`, `minimal`, and `blank` — which declares no
KPIs and an empty DAG, for a tenant whose extract does not match any shipped
contract. Fill it in with `plan_kpis` and `confirm_kpis` below.

---

## `plan_kpis` — derive KPIs from a tenant's own extract

```bash
uv run python -m kpi_engine.cli.plan_kpis --company $C --from-csv extract.csv
uv run python -m kpi_engine.cli.plan_kpis --company $C --from-csv extract.csv --no-llm
uv run python -m kpi_engine.cli.plan_kpis --company $C --from-csv extract.csv --json
```

Stages the upload, profiles it, and proposes which of the 19 catalogue KPIs the
file can support. Writes `configs/_draft/kpi_plan.json`; changes nothing else.

The upload lands in `data/_staging/`, **outside** every declared
`SourceSpec.path`, so the company keeps reporting `awaiting_data` and `ask` keeps
refusing it. That is what stops a tenant being briefly answerable against a
contract written for a different file.

| flag | default | description |
|---|---|---|
| `--company` | — | Required. |
| `--from-csv` | — | Required. The extract to plan against. |
| `--source-id` | the primary | Which declared source this file is. |
| `--plan-id` | a timestamp | Names the draft. |
| `--model` | `gemini-3.5-flash-lite` | Override the model. |
| `--no-llm` | off | Exact column-name matching only. No API key needed. |
| `--json` | off | The plan as JSON — the same shape `/kpi-plan/sync` returns. |

Two things run in order. **Exact name matching** binds every alias whose synonym
equals a column under `normalise` (lowercase, strip non-alphanumerics); on a
well-named extract this alone binds most of the vocabulary, and what it binds it
binds correctly. **The model** is then shown that result as settled and asked only
to place what is left — so `--no-llm` proposes fewer KPIs, never wrong ones.

A KPI the file cannot support is reported in `unavailable[]` with the columns it
looked for, which is more useful than its absence. A variant that does not survive
aggregation to a grain (`Beg. Inventory + Purchases - End. Inventory` summed over
a week counts the same goods twice) is offered but never auto-selected.

---

## `confirm_kpis` — commit the plan and warm the pipeline

```bash
uv run python -m kpi_engine.cli.confirm_kpis --company $C --accept-all
uv run python -m kpi_engine.cli.confirm_kpis --company $C \
    --accept "Gross Profit Margin" --accept ROAS
uv run python -m kpi_engine.cli.confirm_kpis --company $C --accept-all \
    --reject "Churn Rate" --bind "ROAS.cost_of_ads=Paid Media Outlay" \
    --time-grain week --entity-keys Region
```

| flag | default | description |
|---|---|---|
| `--plan-id` | the current draft | Refuses a stale id rather than merging onto a plan that has changed. |
| `--accept-all` | on when no `--accept` | Take every *recommended* KPI. |
| `--accept KPI` | — | Repeatable. Take this one, including an opt-in variant. |
| `--reject KPI` | — | Repeatable. Drop it. |
| `--variant KPI=ID` | — | Compute a KPI a different way; see the plan's `alternatives`. |
| `--bind KPI.ALIAS=COL` | — | Override one measure's column. Still validated. |
| `--time-grain`, `--entity-keys`, `--date-column` | the plan's | Override what was inferred. |
| `--contract-id` | `<slug>_auto_v1` | Names the written contract. |
| `--no-warm-up` | off | Write the configuration but do not run the pipeline. |
| `--no-llm` | off | Skip the causal call. Deterministic edges and catalogue levers still apply. |

Writes four files — the source spec, `semantics/kpis.yaml`, `causal/dag.yaml` and
`company.yaml` — after building and validating all four in memory, so a forbidden
edge or a cycle fails before anything is touched. What it replaces is archived to
`configs/_superseded/<plan_id>/`, and `configs/_confirmed/kpi_plan.json` records
where every single binding came from (`synonym`, `llm`, or `user`) — the
provenance `write_yaml`'s `safe_dump` cannot keep, since it drops comments.

It then accepts the staged CSV through the *ordinary* `attach_source_data`, header
gate included. That gate now checks the file against the contract just synthesised
from it, so a binder bug fails loudly here instead of becoming a panel of NaN.

The DAG is split: KPI nodes, measure nodes and every `deterministic` edge are
derived from the contract's own arithmetic, and `forbidden_edges` is seeded with
the reverse of each. Only `causal` edges and lever ownership are proposed, and an
edge pointing at a KPI node is dropped — the algebraic layer already attributes
that movement exactly.

A **warm-up failure does not roll back** the configuration: the contract validated
and the file passed the header gate, so a thin history is not a reason to revert a
tenant to KPIs it never chose.

---

## `profile_source`

```bash
uv run python -m kpi_engine.cli.profile_source --company acme-retail
uv run python -m kpi_engine.cli.profile_source --company acme-retail --source-id scm_weekly
```
| flag | default | description |
|---|---|---|
| `--source-id` | the company's primary | Which declared source to use. |
| `--out` | `data/profiles/<source_id>.json` | Output path, company-relative. |

---

## `inject_scenario`

```bash
uv run python -m kpi_engine.cli.inject_scenario --company acme-retail
uv run python -m kpi_engine.cli.inject_scenario --company acme-retail --scenario configs/scenarios/ad_cost_shock.yaml
```
| flag | default | description |
|---|---|---|
| `--scenario` | `configs/scenarios/ad_cost_shock.yaml` | Scenario YAML, company-relative. |
| `--source-id` | the company's primary | Which declared source to inject into. |
| `--out-dir` | `data/generated` | Output directory, company-relative. |

---

## `compute_kpis`

```bash
uv run python -m kpi_engine.cli.compute_kpis --company acme-retail --entity-keys Region --time-grain week
```
| flag | default | description |
|---|---|---|
| `--source-id` | the company's primary | Which declared source to use. |
| `--entity-keys` | contract default | Dimensions to slice by (e.g. `Region Channel`). Omit for total level. |
| `--kpis` | all | Subset of KPI names to compute. |
| `--time-grain` | contract default | `day` \| `week` \| `month`. |
| `--run-id` | timestamped | Run id / output directory name. |
| `--dataset` | source config's path | Override the CSV path. |

---

## `profile_series`

```bash
uv run python -m kpi_engine.cli.profile_series --company acme-retail --entity-keys Region --time-grain week
uv run python -m kpi_engine.cli.profile_series --company acme-retail --entity-keys Region --time-grain week
```
| flag | default | description |
|---|---|---|
| `--source`, `--contract`, `--entity-keys`, `--kpis`, `--time-grain` | see `compute_kpis` | same common flags |
| `--eda` | the company's `configs.eda` | Override the EDA spec. Company-relative. |
| `--run-id` | timestamped | Run id. |
| `--dataset` | source config's path | Override the CSV path. |

---

## `detect_anomalies`

```bash
uv run python -m kpi_engine.cli.detect_anomalies --company acme-retail --entity-keys Region --time-grain week
```
| flag | default | description |
|---|---|---|
| `--source`, `--contract`, `--entity-keys`, `--kpis`, `--time-grain` | see `compute_kpis` | same common flags |
| `--detection` | the company's `configs.detection` | Override the detection spec. Company-relative. |
| `--dataset` | source config's path | Override the CSV path. |
| `--run-id` | timestamped | Run id. |

---

## `explain_event`

```bash
uv run python -m kpi_engine.cli.explain_event --company acme-retail --entity-keys Region --time-grain week --top 3
uv run python -m kpi_engine.cli.explain_event --company acme-retail --event-id <id> --run-id <run_id>
```
| flag | default | description |
|---|---|---|
| `--source`, `--contract`, `--entity-keys`, `--kpis`, `--time-grain` | see `compute_kpis` | same common flags |
| `--detection` | the company's `configs.detection` | Override the detection spec. Company-relative. |
| `--graph` | the company's `configs.graph` | Override the causal DAG. Company-relative. |
| `--run-id` | latest | Detection run to read attributions for. |
| `--event-id` | none | Explain one specific event id instead of a top-N sweep. |
| `--top` | `3` | Explain the N highest-scoring events. |
| `--dataset` | source config's path | Override the CSV path. |

---

## `evaluate`

```bash
uv run python -m kpi_engine.cli.evaluate --company acme-retail --run-id <run_id> --truth data/generated/ground_truth.json
```
| flag | default | description |
|---|---|---|
| `--run-id` | latest | Run to score. |
| `--truth` | `data/generated/ground_truth.json` | Ground-truth JSON, company-relative. |
| `--tolerance-days` | `7` | Window-overlap tolerance for a detection to count as a match. |

---

## `run_pipeline` — everything at once

```bash
uv run python -m kpi_engine.cli.run_pipeline --company acme-retail --entity-keys Region --time-grain week --evaluate
```
| flag | default | description |
|---|---|---|
| `--source-id` | the company's primary | Which declared source to use. |
| `--detection` | the company's `configs.detection` | Override the detection spec. Company-relative. |
| `--eda` | the company's `configs.eda` | Override the EDA spec. Company-relative. |
| `--graph` | the company's `configs.graph` | Override the causal DAG. Company-relative. |
| `--dataset` | source config's path | Override the CSV path. |
| `--entity-keys` | none | Dimensions to slice by (e.g. `Region Channel`). |
| `--time-grain` | `day` \| `week` \| `month` | Time grain. |
| `--kpis` | all | Subset of KPI names. |
| `--run-id` | timestamped | Run id. |
| `--top` | `3` | Number of top events to explain. |
| `--skip-profile` | off | Skip stage 0 (source profiling); reuses the cached profile. |
| `--skip-eda` | off | Skip stage 1b (series profiling). |
| `--evaluate` | off | Also run stage 4 scoring against ground truth. |
| `--truth` | `data/generated/ground_truth.json` | Ground-truth JSON, company-relative; only with `--evaluate`. |

---

## `export_schemas`

```bash
uv run python -m kpi_engine.cli.export_schemas
uv run python -m kpi_engine.cli.export_schemas --out-dir schemas
```
| flag | default | description |
|---|---|---|
| `--out-dir` | `schemas` | Directory to write JSON Schema files for every config/payload model. |

---

## `generate_scm`

```bash
uv run python -m kpi_engine.cli.generate_scm --company acme-retail
uv run python -m kpi_engine.cli.generate_scm --company acme-retail --sales data/generated/ad_cost_shock_v1.csv \
    --disruption-start 2026-08-10 --disruption-end 2026-09-07 --shape ramp
```
| flag | default | description |
|---|---|---|
| `--sales` | `data/generated/ad_cost_shock_v1.csv` | Sales CSV, company-relative. |
| `--out` | `data/generated/scm_weekly_v1.csv` | Output SCM CSV, company-relative. |
| `--manifest` | `data/generated/scm_ground_truth.json` | Output manifest, company-relative. |
| `--seed` | `42` | Random seed (deterministic across reruns). |
| `--date-column` | `Date` | Date column name in the sales CSV. |
| `--entity-columns` | `Region`, `Product category` | Entity columns to key the aggregation by. |
| `--disruption-start` | `2026-08-10` | Start date of the planted supplier disruption. |
| `--disruption-end` | `2026-09-07` | End date of the planted supplier disruption. |
| `--shape` | `ramp` | `step` \| `ramp` \| `spike` \| `decay` — disruption shape over time. |

---

## `ask` — the LLM agent

Requires `uv sync --extra dev --extra agent` and, unless `--no-llm` is passed,
`GOOGLE_API_KEY` in `.env`.

```bash
uv run python -m kpi_engine.cli.ask --company acme-retail "why did margin fall in the West?" --persona ops
uv run python -m kpi_engine.cli.ask --company acme-retail "what needs attention?" --persona exec
uv run python -m kpi_engine.cli.ask --company acme-retail "why did ROAS drop?" --model gemini-3.7-flash
uv run python -m kpi_engine.cli.ask --company acme-retail "what needs attention?" --persona exec --no-llm
```

```
uv run python -m kpi_engine.cli.ask --company acme-retail \
  "Net Profit Margin and Inventory Turnover fell in August and September 2026. \
Did the Kestrel Logistics supplier disruption cause it? Compare Kestrel Logistics \
against Northwind Foods and trace the path through to COGS." \
  --persona analyst --time-grain week --entity-keys Supplier
```

```
uv run python -m kpi_engine.cli.ask --company acme-retail \
  "Why did CAC rise and ROAS fall in the West region between mid-March and early \
April 2026, and how much of the CAC move came from marketing spend versus lost \
new customers?" \
  --persona analyst --time-grain week --entity-keys Region
```
| flag | default | description |
|---|---|---|
| `--persona` | inferred | `analyst` \| `exec` \| `ops`. Who is asking; controls which facts and sections are included. |
| `--company` | — | Which tenant to ask. Required. |
| `--sources` | every declared source | Restrict the run to these declared source ids. |
| `--run-id` | timestamped | Run id / output directory name. |
| `--top-events` | `5` | Max events considered for the answer. |
| `--model` | `gemini-3.5-flash-lite` | Model id override. The default is `llm.DEFAULT_MODEL`. |
| `--time-grain` | planner's choice | `day` \| `week` \| `month`. Forces the grain over whatever the planner proposes. Coarse grains starve the baseline. |
| `--entity-keys` | planner's choice | Forces the slicing dimensions. Pass with no values for total level. |
| `--facts` | off | Print every fact, not only the ones the narrative cites. |
| `--plain` | off (implied when stdout is not a terminal) | Print the raw report markdown instead of the terminal view. Byte-identical to the written file. |
| `--json` | off | Print only the report JSON path, no markdown. |
| `--no-llm` | off | Deterministic path only — broad sweep, template-rendered report, no API key needed. |
| `-v`, `--verbose` | off | Include debug-level stage output. |
| `-q`, `--quiet` | off | Suppress the stage log; print only the report. |

Reads both source configs, both KPI contracts, the DAG, the detection spec,
and the persona config. Writes `user/<company>/outputs/<run_id>/agent_report.md` and
`agent_report.json`, plus a full pipeline run per source under
`user/<company>/outputs/<run_id>/<source_id>/`.

---

## Serve it — `python -m kpi_api`

```bash
uv sync --extra api                            # pulls the agent extra with it
uv run python -m kpi_api                       # 127.0.0.1:8000
uv run python -m kpi_api --port 8080 --host 0.0.0.0 --reload
```

`uv sync` is **exact**: it uninstalls every extra you do not name. `uv sync
--extra api` therefore gives a working server but takes `rich` and `pytest` away,
so the terminal view degrades to plain text and the tests will not run. To keep
everything, name everything:

```bash
uv sync --extra dev --extra agent --extra cli --extra api
```

(`uv run` on its own does not remove anything, so only `uv sync` has this effect.)
| flag | default | description |
|---|---|---|
| `--host` | `127.0.0.1` (`KPI_API_HOST`) | Bind address. Localhost by default: these routes carry no authentication. |
| `--port` | `8000` (`KPI_API_PORT`) | 3000 is the frontend and 3001 is the NestJS server. |
| `--reload` | off | Reload on source change. |
| `--log-level` | `info` | uvicorn's level, not the agent's. |

Also read: `CORS_ORIGIN` (comma-separated, default
`http://localhost:3000,http://localhost:3001`) and `KPI_API_MAX_CONCURRENT_RUNS`
(default 2 — a run is CPU-bound, so four at once is four times as slow rather
than four at once).
| route | returns |
|---|---|
| `GET /health` | status, default model, whether a key is present (never the key), company count. |
| `GET /companies` | every registered tenant: sources, agent defaults, `status`, `problems`. |
| `POST /companies` | provision a tenant. 201, or 409 on a duplicate slug, or 422 on a bad one. |
| `GET /company?company=` | one tenant, as above. 404 if unregistered. Singular, because `GET /companies` is already the list. |
| `POST /sources/data?company=&source_id=` | attach a CSV (multipart `file`). 422 naming the missing columns if it does not match the contract. |
| `POST /kpi-plan?company=` | `text/event-stream` — stage a CSV (multipart `file`) and propose a KPI configuration from it. |
| `POST /kpi-plan/sync?company=` | the same, folded into one JSON object. What NestJS calls. |
| `GET /kpi-plan?company=` | the current draft, or 404. Lets a caller resume a handshake it did not start. |
| `POST /kpi-plan/confirm?company=` | `text/event-stream` — write the configs, accept the data, warm up. |
| `POST /kpi-plan/confirm/sync?company=` | the same, folded. |
| `POST /ask?company=` | `text/event-stream` — one typed event per stage, as it lands. |
| `POST /ask/sync?company=` | the same events folded into one JSON object. |
| `GET /run?company=&run_id=` | the saved `agent_report.json`. |
| `GET /run/report?company=&run_id=` | the saved `agent_report.md`. |
| `GET /docs` | the generated OpenAPI page. |

**Nothing is addressed by a path segment.** Every path above is a fixed literal
and every selector — `company`, `source_id`, `run_id` — is a query parameter, so
a client builds one constant string and varies a parameter dict instead of
assembling URLs by interpolation.

`?company=` is required on every scoped route, resolved by one dependency with
one set of failure modes: an unregistered tenant is a 404 raised before the body
is parsed, and omitting the parameter is a 422 naming it — the invariant that
there is no default company, enforced where a caller reads it. The selectors are
parameters rather than body fields because three of these routes carry a *file*
in the body and could not be scoped by one.

`run_id` is the only selector that reaches the filesystem. As a parameter it
arrives already decoded, so a `../..` is refused with a 400 as sent rather than
as whatever path normalisation left behind. Asking a company whose data has not
arrived is a 409, not an empty report.

The `ask` body mirrors the `ask` flags above: `question`, `persona`,
`time_grain`, `entity_keys`, `model`, `no_llm`, `top_events`, `run_id`,
`sources` (declared source ids), plus `logs` (default true). Unknown fields are a
422 rather than a silent drop. `entity_keys: null` means the planner decides;
`entity_keys: []` means total level.

**No field names a file.** The body once carried nine project-root-relative path
strings; `extra="forbid"` guards field names, never their values, so on an
unauthenticated API they were an arbitrary file read. A source is named by the id
its company declared.

There is deliberately no date field. A period reaches the pipeline only through
the planner's intent, which becomes a *report window*; accepting one here is the
easy way to have it arrive as a load filter that starves the baseline instead.

### Events, in the order the graph produces them
| event | carries |
|---|---|
| `started` | `run_id`, `company`, `question`, `persona`, `model`, `no_llm`. |
| `log` | one stage-log line: `level`, `source` (`engine` \| `model`), `message`. The same commentary the terminal prints. Suppressed by `"logs": false`. |
| `ingest` | per-source row and column counts, `coverage_days`, `min_train_periods`. |
| `plan` | the `AnalysisIntent`, twice: `stage: "proposed"` (model call #1) then `"resolved"` (after validation), with `problems[]` for anything the validator adjusted. |
| `engine` | `per_source[]` counts (KPIs, flags, events, explained), twice: `stage: "pipelines"` then `"links"`. |
| `evidence` | the whole fact table, plus events, links, abstentions, freshness, caveats and levers. |
| `narrative` | the `Narrative`, with `attempt`, `used_fallback`, `fallback_reason`. Fires more than once when the repair loop runs. |
| `verification` | `passed`, `violations[]`, counts, with the matching `attempt`. |
| `telemetry` | the deterministic-vs-model split: stages, ms, calls, tokens, cost. |
| `report` | `report_markdown`, `report_path`, `report_json_path`. |
| `clarification` | terminal. The question could not be resolved; nothing was computed. |
| `no_findings` | terminal. What was searched, and what each source produced. |
| `error` | terminal. Yielded rather than raised, so a stream that already delivered a plan says what went wrong. |
| `done` | `outcome` (`report` \| `clarification` \| `no_findings` \| `error`), `duration_ms`. |

A run keeps going after a client disconnects and still writes
`user/<company>/outputs/<run_id>/`, so the answer stays recoverable from
`GET /run?company=...&run_id=...`.

### Onboarding events

`POST /kpi-plan?company=...`:

| event | carries |
|---|---|
| `started` | `plan_id`, `company`, `source_id`, `model`, `no_llm`. |
| `staged` | `rows`, `columns`, `header[]`, and any warnings about the upload itself. |
| `profile` | `n_rows`, `redundant_columns{}`, `duplicate_groups[]`, `coverage[]`. The slow step. |
| `plan` | the `KpiPlan`, twice: `stage: "matched"` (exact name matching alone) then `"resolved"` (after the model and its validator). |
| `bindings` | what the model added beyond exact matching, and every proposal that was dropped, with the reason. |
| `drafted` | `plan_id`, `proposed[]`, `recommended[]`, `unavailable[]`, `problems[]`, token counts. |
| `done` | `outcome` (`drafted` \| `error`), `duration_ms`. |

`POST /kpi-plan/confirm?company=...`:

| event | carries |
|---|---|
| `started` | `plan_id`, `run_id`, `company`, `model`, `no_llm`, `warm_up`. |
| `graph` | `edges_proposed`, `edges_added`, `levers[]`, and each rejected edge with why. |
| `configs` | `contract_id`, `graph_id`, `kpis[]`, `entity_columns`, `time_grain`, edge counts, `levers[]`, `written[]`, `superseded`. **The point of no return** — the tenant is configured, and every later failure is about the data rather than the decision. |
| `data` | `rows`, `ready`, and `attach_source_data`'s warnings. |
| `engine` | one per source: `flags`, `events`, `bundles`, `seconds` — or `error`, which does not undo the configuration. |
| `done` | `outcome` (`ready` \| `configured` \| `warm_up_failed` \| `error`), `run_id`, `duration_ms`. |

A decision that does not validate is a **422 with nothing written** — the merge
and synthesis run inside the request, before the stream opens. A `plan_id` that is
not the current draft is a **409**: someone re-proposed in between, so re-read the
draft rather than merging stale decisions onto it. No draft at all is a **404**.

There is no job registry. The warm-up writes `outputs/<run_id>/` like any run, and
`started` names that `run_id` before any work begins, so a client that hangs up
polls `GET /run?company=...&run_id=...` exactly as it would for `/ask`.

```bash
curl localhost:8000/companies

# deterministic, no key needed
curl -N -X POST 'localhost:8000/ask?company=acme-retail' -H 'content-type: application/json' \
  -d '{"question":"what needs attention?","no_llm":true,"persona":"exec"}'

# the two-model-call path
curl -N -X POST 'localhost:8000/ask?company=acme-retail' -H 'content-type: application/json' -d '{
  "question":"Why did CAC rise and ROAS fall in the West region between mid-March and early April 2026?",
  "persona":"analyst","time_grain":"week","entity_keys":["Region"]}'

# provision a tenant, then give it data
curl -X POST localhost:8000/companies -H 'content-type: application/json' \
  -d '{"company_id":"demo-co","display_name":"Demo Co","domains":["retail"]}'
curl -F file=@extract.csv 'localhost:8000/sources/data?company=demo-co&source_id=retail_daily'

# ...or, when the extract matches no shipped contract, derive one from it
curl -X POST localhost:8000/companies -H 'content-type: application/json' \
  -d '{"company_id":"odd-co","display_name":"Odd Co","domains":["other"],"template":"blank"}'
curl -F file=@extract.csv 'localhost:8000/kpi-plan/sync?company=odd-co&no_llm=false'
curl 'localhost:8000/kpi-plan?company=odd-co'           # read the draft back
curl -N -X POST 'localhost:8000/kpi-plan/confirm?company=odd-co' \
  -H 'content-type: application/json' -d '{
  "plan_id":"kpiplan-20260828-041932",
  "decisions":[{"name":"Churn Rate","verdict":"reject"}]}'
curl 'localhost:8000/company?company=odd-co'            # status: ready
```

---

## Tests

```bash
uv run pytest tests/ -q                                  # full suite
uv run pytest tests/test_agent.py -q                      # agent layer only
uv run pytest tests/test_eda.py -q                         # EDA layer only
uv run pytest tests/test_causal.py::test_shapley_efficiency_axiom -q   # single test
```
