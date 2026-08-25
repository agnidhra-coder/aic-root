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
| `--model` | `gemini-3.6-flash` | Model id override. |
| `--json` | off | Print only the report JSON path, no markdown. |
| `--no-llm` | off | Deterministic path only — broad sweep, template-rendered report, no API key needed. |

Reads both source configs, both KPI contracts, the DAG, the detection spec,
and the persona config. Writes `outputs/<run_id>/agent_report.md` and
`agent_report.json`, plus a full pipeline run per source under
`outputs/<run_id>/<source_id>/`.

---

## Tests

```bash
uv run pytest tests/ -q                                  # full suite
uv run pytest tests/test_agent.py -q                      # agent layer only
uv run pytest tests/test_eda.py -q                         # EDA layer only
uv run pytest tests/test_causal.py::test_shapley_efficiency_axiom -q   # single test
```
