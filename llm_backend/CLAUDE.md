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
uv sync --extra dev --extra agent --extra cli    # setup (CPython 3.11, editable install)
uv run pytest tests/ -q                          # 152 tests
uv run pytest tests/test_causal.py::test_shapley_efficiency_axiom -q   # single test

uv run python -m kpi_engine.cli.profile_source   # stage 0: schema, redundancy, coverage
uv run python -m kpi_engine.cli.inject_scenario  # stage 0b: ground-truth events
uv run python -m kpi_engine.cli.compute_kpis     # stage 1: KPI panel
uv run python -m kpi_engine.cli.profile_series   # stage 1b: trend/seasonality/quality
uv run python -m kpi_engine.cli.detect_anomalies # stage 2: flags -> event windows
uv run python -m kpi_engine.cli.explain_event    # stage 3: attribution + evidence
uv run python -m kpi_engine.cli.evaluate         # stage 4: score vs ground truth
uv run python -m kpi_engine.cli.export_schemas   # JSON Schema for LLM-authored configs
uv run python -m kpi_engine.cli.generate_scm     # stage 0c: derive the weekly SCM source

# ask a question (two model calls; key read from .env)
uv run python -m kpi_engine.cli.ask "why did margin fall in the West?" --persona ops
uv run python -m kpi_engine.cli.ask "why did ROAS drop?" --model gemini-3.7-flash
uv run python -m kpi_engine.cli.ask "what needs attention?" --persona exec --no-llm
# force the configuration rather than trusting the planner (see README for the two demo questions)
uv run python -m kpi_engine.cli.ask "..." --persona analyst --time-grain week --entity-keys Supplier

# end to end
uv run python -m kpi_engine.cli.run_pipeline --entity-keys Region --time-grain week \
    --dataset data/generated/ad_cost_shock_v1.csv --evaluate
```

Common flags: `--entity-keys` (slice dimensions), `--time-grain day|week|month`,
`--kpis`, `--dataset`, `--run-id`. Artefacts land in `outputs/<run_id>/`.

## Architecture

Stages chain through `outputs/<run_id>/`, each runnable alone:
`profile → panel → [series profile] → detect → attribute → evaluate`.
See `RUNBOOK.md` for what each stage reads, writes, and what to look for.

- `contracts/` — Pydantic models for every config and payload. Nothing in the
  engine reads a raw dict; an invalid config fails before any computation.
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
  argv-chaining the CLIs. What `kpi_agent` calls. Verified against the CLI chain
  by `test_in_process_pipeline_reproduces_the_cli_chain`.

`src/kpi_agent/` — the LangGraph layer:
`ingest → plan → validate → run → link → facts → narrate → verify → report`.
- `catalog.py` — metadata-only view of the sources; what the planner may see.
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
- **The DAG is declared, not discovered.** Add edges to
  `configs/causal/retail_dag.yaml` with direction from domain knowledge; the
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
- **Thresholds are calibrated, not guessed.** After touching
  `configs/detection/default.yaml`, re-run `cli.evaluate` and check recall did not
  fall. Current baseline: recall 100%, precision 54.5%, F1 0.706.
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
- **`rich` is presentation, never capability.** It lives in the `cli` extra and is
  imported lazily behind `_common.console()`. Every path has a plain-text answer;
  a missing dependency costs colour and box-drawing, never a line of output and
  never a computed number.
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

- `data/raw/dummy_data.csv` is **statistically flat** — no trend, seasonality, or
  autocorrelation. Detections on it are noise by construction. Use
  `data/generated/ad_cost_shock_v1.csv` for anything that needs real signal.
- Exact redundancies: `Net Sales == Total Revenue == Retail Sales`,
  `Number of Sales == Total Transactions`, `Total Gross Profit == Net Sales − COGS`,
  `End Customers == Start + New − Lost`.
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

- **A KPI**: add to `configs/semantics/retail_kpis.yaml` (measures + expression +
  materiality + guards), then add its nodes and deterministic edges to the DAG.
- **A detector**: implement it in `detection/`, return `list[Flag]`, call it from
  `detection/runner.py`. Windowing and everything downstream is additive.
- **A causal method**: add the estimator, then a branch in `causal/router.py` that
  states its preconditions and populates `MethodChoice.considered` on rejection.
- **A data source**: subclass `DataSource`, `register_source(...)`, extend the
  `SourceSpec.type` literal.
- **A persona**: add it to `configs/agent/personas.yaml` (`include_facts`,
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
