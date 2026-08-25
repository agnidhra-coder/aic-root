# KPI Anomaly Detection & Causal Attribution Engine

A KPI intelligence engine in two halves, with a boundary between them that is
enforced rather than described.

`kpi_engine` is deterministic: it detects material KPI movements, decomposes them
across their drivers, and emits a structured evidence bundle. **No LLM is involved
anywhere in it**, and none ever should be — the evidence bundle is the numeric
ground everything else stands on.

`kpi_agent` is a LangGraph layer that takes a question in plain language, runs the
engine, and answers. It calls a model exactly twice: once to turn the question
into a *validated* configuration, once to write the prose. It never calls one to
produce a quantity.

## Quick start

```bash
uv sync --extra dev --extra agent --extra cli

# Ask a question. Two model calls at the ends; deterministic engine in between.
# GOOGLE_API_KEY is read from a gitignored .env.
uv run python -m kpi_engine.cli.ask "why did margin fall in the West?" --persona ops

# Any Gemini model; the default is gemini-3.5-flash-lite.
uv run python -m kpi_engine.cli.ask "why did ROAS drop?" --model gemini-3.7-flash

# The same thing with no API key: broad sweep, template-rendered report.
uv run python -m kpi_engine.cli.ask "what needs attention?" --persona exec --no-llm
```

The `cli` extra adds `rich`, which is what separates the two halves on screen: the
plan and the prose sit in panels labelled as the model's, everything else is
labelled as the engine's, and the run logs each stage prefixed `[engine]` or
`[model]` while it happens. Without the extra the same report prints as plain
markdown. `--plain` forces that anyway, and is implied when stdout is a pipe, so
`ask --plain > report.md` and `outputs/<run>/agent_report.md` are the same bytes.

### Asking a question that gets a real answer

Two things decide whether the engine has anything to say.

**Ask about a KPI, not a driver.** The answerable set is CAC, ROAS, Net Profit
Margin, Conversion Rate, Inventory Turnover, Fill Rate, Weighted Lead Time,
Stockout Rate and Days Of Supply. COGS, Total Expenses and Cash are *drivers*: the
engine attributes movements to them, so naming one as the thing that moved is
asking for the answer as the question, and you get an abstention.

**Grain is bounded below by noise and above by history.** The detector trains its
baseline on `min_train_periods` *periods*, so a coarse grain over a short span
leaves it nothing to learn from and it correctly reports that nothing happened.
Over these two years, `month` gives 24 periods against a requirement of 56 and
finds nothing at all. `validate_intent` now catches that and steps the grain down,
saying so in the plan panel; `--time-grain` overrides the planner outright.

Two questions that land squarely on the injected scenario:

```bash
# The cross-source story: a supplier disruption reaching margin through COGS.
# Slicing by Supplier is what gives the estimator a control group -- Northwind is
# untreated. Slicing by Region instead marks all five regions as affected and
# collapses confidence, because the disruption is filtered on Supplier.
uv run python -m kpi_engine.cli.ask \
  "Net Profit Margin and Inventory Turnover fell in August and September 2026. \
Did the Kestrel Logistics supplier disruption cause it? Compare Kestrel Logistics \
against Northwind Foods and trace the path through to COGS." \
  --persona analyst --time-grain week --entity-keys Supplier

# The clean one: a West-only ad shock, with four untreated regions as donors.
# Ground truth puts the CAC split at ln(1.15) : -ln(0.80), so the report is
# checkable against an arithmetic answer.
uv run python -m kpi_engine.cli.ask \
  "Why did CAC rise and ROAS fall in the West region between mid-March and early \
April 2026, and how much of the CAC move came from marketing spend versus lost \
new customers?" \
  --persona analyst --time-grain week --entity-keys Region
```

`--persona analyst` matters for the first: `ops` lists `Purchase Price Per Unit`
among its `restricted_entitlements`, and that is the driver the cross-source edge
runs through.

Or drive the stages directly:

```bash
# 0. What is this dataset, really?
uv run python -m kpi_engine.cli.profile_source

# 0b. Manufacture events with known causes (see "Why inject data" below)
uv run python -m kpi_engine.cli.inject_scenario

# 0c. Derive the weekly supply-chain source from the sales file
uv run python -m kpi_engine.cli.generate_scm

# 1-4. Detect, attribute, score
uv run python -m kpi_engine.cli.run_pipeline \
    --entity-keys Region --time-grain week \
    --dataset data/generated/ad_cost_shock_v1.csv --evaluate
```

## Pipeline

```
 source config ──► [0] profile ──────► data_profile.json
                        │              duplicates, accounting identities, grain coverage
                        ▼
 KPI contract  ──► [1] KPI panel ────► kpi_panel.parquet
                        │              aggregate measures, THEN apply formula
                        ▼
 detection cfg ──► [2] detect ───────► flags.json ──► events.json
                        │              STL ▸ expanding baseline ▸ MAD-z / PELT+CUSUM / Mahalanobis
                        ▼
 causal DAG    ──► [3] attribute ────► evidence_bundles.json
                        │              Layer A exact algebra ▸ Layer B DiD | ITS | DML
                        ▼
 ground truth  ──► [4] evaluate ─────► evaluation.json + run_manifest.json
```

Every stage is independently runnable and reads the previous stage's output from
`outputs/<run_id>/`.

## Two things the data forced

**The supplied dataset has no signal.** 20,000 rows over 730 days, and monthly
means of every KPI are flat across all 24 months; lag-1 autocorrelation of daily
revenue is 0.01; there is no weekday effect. Detectors run against it surface
tail noise and causal estimators recover approximately zero. Neither module could
be shown to work, or shown to be *calibrated*. So `cli.inject_scenario` stamps
movements with known causes onto the base data and writes a ground-truth
manifest, and `cli.evaluate` scores against it. Every threshold in
`configs/detection/default.yaml` was set by measuring, not by guessing.

**The schema is full of exact redundancy.** `Net Sales == Total Revenue ==
Retail Sales`, `Number of Sales == Total Transactions`, `Total Gross Profit ==
Net Sales − COGS`, `End Customers == Start + New − Lost`. Fed to a regression as
independent drivers, perfectly collinear columns produce an arbitrary split of
the true effect: the fit looks fine and the attribution is meaningless. Stage 0
finds them and the causal layer refuses to use them. The accounting is careful —
*k* columns bound by one equation carry *k−1* independent columns, so exactly one
is dropped, not all *k*. Dropping all of them would delete revenue from the
driver set entirely.

## Design decisions that matter

**Ratio-of-sums, never mean-of-ratios.** A KPI contract declares its base
measures separately from its arithmetic. Measures are aggregated to the grain
first, then the formula runs on those sums. Averaging per-row ratios gives a
different, wrong answer whenever denominators vary across rows — which they
always do. Tested directly against the alternative in `tests/test_semantics.py`.

**Formulas are parsed, never `eval`'d.** `semantics/expressions.py` walks the AST
and admits only arithmetic over declared measure aliases plus a short function
whitelist. Formula strings come from config today and from an LLM tomorrow; they
are untrusted input either way.

**The baseline never sees the future.** Expectations for period *t* come from a
model fitted strictly on data before *t*. A baseline fitted on the whole series
understates residuals, hides the anomalies it exists to find, and reports a
precision it could not reproduce live. `tests/test_detection.py` asserts that
truncating the series leaves earlier expectations unchanged.

**Three detector modalities, because they fail differently.** The injected shock
moves each individual period by roughly 2σ — too little for point detection to
call — while moving the *segment mean* by many standard errors. Point detection
asks "is today surprising"; change-point detection asks "has the regime changed".
Only the second question has a good answer for a sustained shift. Mahalanobis
distance catches the third case: each KPI normal alone, the combination
unprecedented.

**Corroboration instead of higher thresholds.** Residuals on ratio KPIs are
heavy-tailed, so a modified z-score flags far more than its nominal tail
probability implies. Raising the threshold suppresses real events too. Requiring
that an event carry several flags *and* two independent detector families is
stable, and measurably so: it took precision from 42.9% to 54.5% at unchanged
100% recall.

**Attribution is two layers, and the split is the honest part.**

| | Layer A | Layer B |
|---|---|---|
| Question | how much of the move came from each of the KPI's own inputs | what moved the upstream input |
| Method | exact Shapley over measures, cross-checked by LMDI | DiD ▸ ITS ▸ cross-fitted DML |
| Assumptions | none — it follows from the KPI's definition | stated per method, preconditions tested |
| Output | contributions summing exactly to the total | effect × observed movement, with CIs |

Layer A is exact because every KPI here is a known function of its measures.
Shapley is the only attribution satisfying efficiency, symmetry and the null
player property, and with ≤6 measures the coalitions are enumerated exactly — no
sampling, so runs are reproducible to the last digit. The naive alternative
("which input changed by the largest percent") is wrong whenever more than one
input moves, which is exactly the multi-driver case the engine exists to explain.

**The router records why it chose, and why it rejected.** DiD needs untreated
donor slices and parallel pre-trends; ITS needs a stable pre-period; DML needs
non-collinear observations. The router tests preconditions, picks the strongest
design the data supports, and emits the whole deliberation. *"DiD, because four
untreated regions were available and pre-trends held (p=0.092)"* is auditable; a
bare number is not.

**Abstention is an output, not a failure.** An engine that always produces an
explanation cannot be trusted. `evidence/abstention.py` returns a structured
payload naming the missing evidence and what would resolve it — on thin support,
short history, failed parallel trends, method contradiction, or low confidence.

**Confidence is a published formula**, a weighted geometric mean over signal-to-
noise, support, history depth, estimator precision and method agreement. The
geometric mean is deliberate: one collapsed factor drags the score down instead
of being averaged away, so an estimate resting on eight rows is not rescued by a
tidy standard error.

## Measured results

Against the injected ground truth, at Region × week grain:

| | |
|---|---|
| Detection recall | **100%** (6/6, including the system-wide event in all 5 regions) |
| Detection precision | 54.5% (5 false positives over 520 region-weeks of pure noise) |
| Top-driver attribution | **100%** (2/2 — `Cost Of Ads` for ROAS, `New Customers` for CAC) |
| Raw uninjected data | 6 events, i.e. no flood — thresholds are not over-firing |

Independent checks: exact Shapley reproduces the hand-computed 38.57 / 61.43
split for a +15% / −20% two-sided move with zero residual; LMDI agrees to 0.06pp
by a different derivation; DML recovers a known effect of 3.0 (CI 2.93–3.11)
where naive OLS returns a confounded 4.38; DiD recovers a planted effect and
abstains when pre-trends are made to diverge.

## Layout

```
src/kpi_agent/     LangGraph layer -- the only place a model is called
  catalog.py       metadata-only view of the sources; what the planner may see
  intent.py        LLM #1 + the validator that resolves or rejects its proposal
  linking.py       cross-source alignment, licensed by the DAG
  facts.py         bundles -> numbered fact table; the narrator's whole context
  narrate.py       LLM #2 + the deterministic template fallback
  llm.py           model configuration, build_llm, usage/cost accounting
  verify.py        mechanical grounding check; no model involved
  graph.py         the state machine
src/kpi_engine/
  contracts/   pydantic models for every config and payload
  sources/     DataSource protocol + csv/excel adapters (registry-dispatched)
  profiling/   redundancy, identities, grain coverage, freshness
  semantics/   restricted expression evaluator, aggregation, panel builder
  detection/   decompose, baseline, point, changepoint, multivariate, windowing
  causal/      dag, algebraic, did, its, dml, router
  scenarios/   ground-truth event injection
  evidence/    confidence, abstention, bundle, telemetry
  cli/         one executable per stage, plus `ask` for the agent
  pipeline.py  the whole chain in one process, returning objects not files
configs/       source, semantics, causal, detection, scenarios  (all hand-authored)
schemas/       exported JSON Schema — the LLM-facing contract surface
```

## Swapping the data source

Write one class implementing `DataSource` (`describe` / `load` / `_max_date`),
register it, extend the `SourceSpec.type` literal. Nothing downstream changes:

```python
register_source("postgres", PostgresSource)
```

## Where the LLM attaches

At two points, both at the edges.

```
question ──► [plan] ──► validate ──┬─► clarify (stop, ask)
   LLM #1                          │
                                   ▼
        profile ▸ panel ▸ detect ▸ attribute ▸ link      ← deterministic
                                   │
                                   ▼
                              fact table
                                   │
                    ┌──────────────┴──────────────┐
              [narrate] ──► verify ──► report     │
               LLM #2         │                   │
                              └── fails twice ────┘  template fallback
```

**In: a configuration, not an analysis.** The planner sees a *catalog* — KPI names,
dimension values, date coverage, grain sufficiency, graph node names. No rows, no
aggregates, no KPI values. It proposes an `AnalysisIntent`; `intent.validate_intent`
then resolves or rejects it against the catalog in ordinary Python. Structured
output guarantees the shape of what comes back, not its truth: nothing stops a
model returning a well-formed intent naming a KPI that does not exist. The
validator is what makes a plausible plan a permitted one. An unresolvable request
gets a clarifying question and computes nothing.

**Out: prose over a numbered fact table.** Every number the narrator may write is
extracted from the evidence bundles first, given an id, and paired with its
lineage. That table is its entire context — it cannot reach the panel, the raw
frame, or the bundles it came from. "Grounded in audited numbers" is then a
property of what the model can see, not a rule it is asked to follow.

**And then it is checked.** `kpi_agent/verify.py` re-reads the narrative and
rejects it mechanically — no second model adjudicating the first. Every numeric
literal must match a fact within tolerance; every claim must cite a resolvable id;
every action must name a lever the graph declares controllable, with the owner the
graph assigns; no cause may be asserted for a KPI whose bundle abstained; and
directional language must agree with the sign of the fact it cites. One repair
pass is allowed, with the violations fed back. A second failure renders the same
report from a deterministic template. **The worst case is a terse true report, not
a fluent false one.**

`RunManifest`'s `llm_calls` / `llm_tokens` / `llm_cost_usd` fields no longer sit at
zero — the report states the split, and the deterministic side was already measured.
Where a model's rates are not known the cost is reported as *not priced* rather
than as zero, which would read as a free run.

**The model is one object, called directly.** `run_agent` builds a single
`ChatGoogleGenerativeAI` and puts it in the run config with the run's token
accounting; `intent.py` and `narrate.py` call
`.with_structured_output(schema, method="json_schema").invoke(...)` on it
themselves. There is no client class wrapping LangChain, so there is no second API
to keep in step with it — and a stub chat model is all the test suite needs, which
is how all 43 agent tests run with no API key. `json_schema` sends the strict
Pydantic schema itself, including the `additionalProperties: false` that
`extra="forbid"` emits, and the reply is validated against that same model on the
way back: a fabricated field is refused twice over.

## Two sources, deliberately mismatched

`data/generated/scm_weekly_v1.csv` is weekly and keyed by supplier; the sales file
is daily and has no supplier dimension. They do not join. But they are not
independent either: the supply file's `Units Received` *is* the sales file's
`Units Sold` aggregated to region × category × week, and its unit price is that
file's COGS per unit. A planted supplier disruption degrades fill rate and lead
time there; the corresponding COGS increase is stamped into the sales file. The
cause and its consequence live in different systems at different grains.

Which is what makes the linking step mean something. With 105 weeks and five
regions, coincidental overlap is abundant — at any usable tolerance most sales
events overlap *some* supply event. So overlap is not the test. `linking.py` emits
a link only where the declared causal graph contains a path from a supply node
that moved to a driver of the sales KPI that moved:

```
Stockout Days → Total Transactions → Units Sold → Net Sales → Net Profit Margin
```

No path, no link, however well the windows line up. A same-region pair with
identical windows and no declared path produces nothing, and there is a test that
says so.

## Tests

```bash
uv run pytest tests/ -q      # 131 tests, no API key required
```

They pin the properties that matter rather than golden outputs: the evaluator
rejects a dozen injection attempts, Shapley satisfies its axioms, the baseline
cannot see the future, injection preserves the accounting identities, DiD abstains
on diverging pre-trends, and DML refuses when a treatment is collinear with a control.

The agent's tests pin its *refusals*, which is where the guarantee lives. The LLM
client is a parameter everywhere, so a stub drives the whole graph with no API key.
The verifier must reject a fabricated number, an unresolvable citation, an uncited
claim, an action on a node nobody controls, an owner the graph does not assign, an
invented confidence score, a cause asserted over an abstention, and a sentence
pointing the opposite way from the fact it cites. Two failed passes must land on
the template — and the template must pass the same verifier, for every persona.
`test_in_process_pipeline_reproduces_the_cli_chain` asserts the agent is running
the same engine the thresholds were calibrated against, event id for event id.
