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

Artefacts land in `outputs/<run_id>/`. Pass `--run-id` to make several stages
share one directory, or omit it to get a timestamped one.

---

## `profile_source`

```bash
uv run python -m kpi_engine.cli.profile_source
uv run python -m kpi_engine.cli.profile_source --source configs/sources/scm_csv.yaml --out outputs/scm_profile.json
```

| flag | default | description |
|---|---|---|
| `--source` | `configs/sources/retail_csv.yaml` | Source config YAML. |
| `--out` | `outputs/profiles/<source_id>.json` | Output JSON path override. |

---

## `inject_scenario`

```bash
uv run python -m kpi_engine.cli.inject_scenario
uv run python -m kpi_engine.cli.inject_scenario --scenario configs/scenarios/ad_cost_shock.yaml --out-dir data/generated
```

| flag | default | description |
|---|---|---|
| `--scenario` | `configs/scenarios/ad_cost_shock.yaml` | Scenario spec YAML (events to stamp). |
| `--source` | `configs/sources/retail_csv.yaml` | Base source config to mutate. |
| `--out-dir` | `data/generated` | Directory to write the mutated CSV + ground truth JSON. |

---

## `compute_kpis`

```bash
uv run python -m kpi_engine.cli.compute_kpis --entity-keys Region --time-grain week
```

| flag | default | description |
|---|---|---|
| `--source` | `configs/sources/retail_csv.yaml` | Source config YAML. |
| `--contract` | `configs/semantics/retail_kpis.yaml` | KPI contract YAML. |
| `--entity-keys` | contract default | Dimensions to slice by (e.g. `Region Channel`). Omit for total level. |
| `--kpis` | all | Subset of KPI names to compute. |
| `--time-grain` | contract default | `day` \| `week` \| `month`. |
| `--run-id` | timestamped | Run id / output directory name. |
| `--dataset` | source config's path | Override the CSV path. |

---

## `profile_series`

```bash
uv run python -m kpi_engine.cli.profile_series --entity-keys Region --time-grain week
uv run python -m kpi_engine.cli.profile_series --entity-keys Region --time-grain week --dataset data/generated/ad_cost_shock_v1.csv
```

| flag | default | description |
|---|---|---|
| `--source`, `--contract`, `--entity-keys`, `--kpis`, `--time-grain` | see `compute_kpis` | same common flags |
| `--eda` | `configs/eda/default.yaml` | EDA spec YAML (decomposition/trend/seasonality settings). |
| `--run-id` | timestamped | Run id. |
| `--dataset` | source config's path | Override the CSV path. |

---

## `detect_anomalies`

```bash
uv run python -m kpi_engine.cli.detect_anomalies --entity-keys Region --time-grain week --dataset data/generated/ad_cost_shock_v1.csv
```

| flag | default | description |
|---|---|---|
| `--source`, `--contract`, `--entity-keys`, `--kpis`, `--time-grain` | see `compute_kpis` | same common flags |
| `--detection` | `configs/detection/default.yaml` | Detection spec YAML (STL, baseline, thresholds). |
| `--dataset` | source config's path | Override the CSV path. |
| `--run-id` | timestamped | Run id. |

---

## `explain_event`

```bash
uv run python -m kpi_engine.cli.explain_event --entity-keys Region --time-grain week --dataset data/generated/ad_cost_shock_v1.csv --top 3
uv run python -m kpi_engine.cli.explain_event --event-id <id> --run-id <run_id>
```

| flag | default | description |
|---|---|---|
| `--source`, `--contract`, `--entity-keys`, `--kpis`, `--time-grain` | see `compute_kpis` | same common flags |
| `--detection` | `configs/detection/default.yaml` | Detection spec YAML. |
| `--graph` | `configs/causal/retail_dag.yaml` | Causal DAG YAML. |
| `--run-id` | latest | Detection run to read attributions for. |
| `--event-id` | none | Explain one specific event id instead of a top-N sweep. |
| `--top` | `3` | Explain the N highest-scoring events. |
| `--dataset` | source config's path | Override the CSV path. |

---

## `evaluate`

```bash
uv run python -m kpi_engine.cli.evaluate --run-id <run_id> --truth data/generated/ground_truth.json
```

| flag | default | description |
|---|---|---|
| `--run-id` | latest | Run to score. |
| `--truth` | `data/generated/ground_truth.json` | Ground-truth JSON to score against. |
| `--tolerance-days` | `7` | Window-overlap tolerance for a detection to count as a match. |

---

## `run_pipeline` — everything at once

```bash
uv run python -m kpi_engine.cli.run_pipeline --entity-keys Region --time-grain week --dataset data/generated/ad_cost_shock_v1.csv --evaluate
```

| flag | default | description |
|---|---|---|
| `--source` | `configs/sources/retail_csv.yaml` | Source config YAML. |
| `--contract` | `configs/semantics/retail_kpis.yaml` | KPI contract YAML. |
| `--detection` | `configs/detection/default.yaml` | Detection spec YAML. |
| `--eda` | `configs/eda/default.yaml` | EDA spec YAML. |
| `--graph` | `configs/causal/retail_dag.yaml` | Causal DAG YAML. |
| `--dataset` | source config's path | Override the CSV path. |
| `--entity-keys` | none | Dimensions to slice by (e.g. `Region Channel`). |
| `--time-grain` | `day` \| `week` \| `month` | Time grain. |
| `--kpis` | all | Subset of KPI names. |
| `--run-id` | timestamped | Run id. |
| `--top` | `3` | Number of top events to explain. |
| `--skip-profile` | off | Skip stage 0 (source profiling); reuses the cached profile. |
| `--skip-eda` | off | Skip stage 1b (series profiling). |
| `--evaluate` | off | Also run stage 4 scoring against ground truth. |
| `--truth` | `data/generated/ground_truth.json` | Ground-truth JSON, used only with `--evaluate`. |

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
uv run python -m kpi_engine.cli.generate_scm
uv run python -m kpi_engine.cli.generate_scm --sales data/generated/ad_cost_shock_v1.csv \
    --disruption-start 2026-08-10 --disruption-end 2026-09-07 --shape ramp
```

| flag | default | description |
|---|---|---|
| `--sales` | `data/generated/ad_cost_shock_v1.csv` | Sales CSV the SCM panel is derived from. |
| `--out` | `data/generated/scm_weekly_v1.csv` | Output SCM CSV path. |
| `--manifest` | `data/generated/scm_ground_truth.json` | Output ground-truth JSON path. |
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
uv run python -m kpi_engine.cli.ask "why did margin fall in the West?" --persona ops
uv run python -m kpi_engine.cli.ask "what needs attention?" --persona exec
uv run python -m kpi_engine.cli.ask "why did ROAS drop?" --model gemini-3.7-flash
uv run python -m kpi_engine.cli.ask "what needs attention?" --persona exec --no-llm
```

```
uv run python -m kpi_engine.cli.ask \
  "Net Profit Margin and Inventory Turnover fell in August and September 2026. \
Did the Kestrel Logistics supplier disruption cause it? Compare Kestrel Logistics \
against Northwind Foods and trace the path through to COGS." \
  --persona analyst --time-grain week --entity-keys Supplier
```

```
uv run python -m kpi_engine.cli.ask \
  "Why did CAC rise and ROAS fall in the West region between mid-March and early \
April 2026, and how much of the CAC move came from marketing spend versus lost \
new customers?" \
  --persona analyst --time-grain week --entity-keys Region
```

| flag | default | description |
|---|---|---|
| `--persona` | inferred | `analyst` \| `exec` \| `ops`. Who is asking; controls which facts and sections are included. |
| `--dataset` | source config's path | Override the sales CSV. |
| `--scm` | source config's path | Override the supply-chain CSV. |
| `--source` | `configs/sources/retail_csv.yaml` | Sales source config YAML. |
| `--scm-source` | `configs/sources/scm_csv.yaml` | Supply-chain source config YAML. |
| `--contract` | `configs/semantics/retail_kpis.yaml` | Sales KPI contract YAML. |
| `--scm-contract` | `configs/semantics/scm_kpis.yaml` | Supply-chain KPI contract YAML. |
| `--graph` | `configs/causal/retail_dag.yaml` | Causal DAG YAML. |
| `--detection` | `configs/detection/default.yaml` | Detection spec YAML. |
| `--personas` | `configs/agent/personas.yaml` | Persona definitions YAML. |
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
and the persona config. Writes `outputs/<run_id>/agent_report.md` and
`agent_report.json`, plus a full pipeline run per source under
`outputs/<run_id>/<source_id>/`.

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
| `GET /health` | status, default model, whether a key is present (never the key). |
| `POST /ask` | `text/event-stream` — one typed event per stage, as it lands. |
| `POST /ask/sync` | the same events folded into one JSON object. |
| `GET /runs/{run_id}` | the saved `agent_report.json`. |
| `GET /runs/{run_id}/report` | the saved `agent_report.md`. |
| `GET /docs` | the generated OpenAPI page. |

The request body mirrors the `ask` flags above: `question`, `persona`,
`time_grain`, `entity_keys`, `model`, `no_llm`, `top_events`, `run_id`,
`dataset`, `scm`, `source`, `scm_source`, `contract`, `scm_contract`, `graph`,
`detection`, `personas`, plus `logs` (default true). Unknown fields are a 422
rather than a silent drop. `entity_keys: null` means the planner decides;
`entity_keys: []` means total level.

There is deliberately no date field. A period reaches the pipeline only through
the planner's intent, which becomes a *report window*; accepting one here is the
easy way to have it arrive as a load filter that starves the baseline instead.

### Events, in the order the graph produces them

| event | carries |
|---|---|
| `started` | `run_id`, `question`, `persona`, `model`, `no_llm`. |
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
`outputs/<run_id>/`, so the answer stays recoverable from `GET /runs/{run_id}`.

```bash
# deterministic, no key needed
curl -N -X POST localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question":"what needs attention?","no_llm":true,"persona":"exec"}'

# the two-model-call path
curl -N -X POST localhost:8000/ask -H 'content-type: application/json' -d '{
  "question":"Why did CAC rise and ROAS fall in the West region between mid-March and early April 2026?",
  "persona":"analyst","time_grain":"week","entity_keys":["Region"]}'
```

---

## Tests

```bash
uv run pytest tests/ -q                                  # full suite
uv run pytest tests/test_agent.py -q                      # agent layer only
uv run pytest tests/test_eda.py -q                         # EDA layer only
uv run pytest tests/test_causal.py::test_shapley_efficiency_axiom -q   # single test
```
