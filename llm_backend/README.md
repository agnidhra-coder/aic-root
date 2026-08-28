# KPI Anomaly Detection & Causal Attribution Engine

A KPI intelligence engine in two halves, with a boundary between them that is
enforced rather than described.

`kpi_engine` is deterministic: it detects material KPI movements, decomposes them
across their drivers, and emits a structured evidence bundle. **No LLM is involved
anywhere in it**, and none ever should be — the evidence bundle is the numeric
ground everything else stands on.

`kpi_agent` is a LangGraph layer that takes a question in plain language, runs the
engine, and answers. It calls a model exactly twice per question: once to turn the
question into a *validated* configuration, once to write the prose. It calls one
twice more per *tenant*, when a company's KPIs have to be worked out from its own
extract rather than copied from a template — once to bind columns to a fixed KPI
catalogue, once to propose the causal mechanisms between them.

It never calls a model to produce a quantity, and never to write a formula.

## Quick start

```bash
uv sync --extra dev --extra agent --extra cli

# Ask a question. Two model calls at the ends; deterministic engine in between.
# GOOGLE_API_KEY is read from a gitignored .env.
uv run python -m kpi_engine.cli.ask --company acme-retail "why did margin fall in the West?" --persona ops

# Any Gemini model; the default is gemini-3.5-flash-lite.
uv run python -m kpi_engine.cli.ask --company acme-retail "why did ROAS drop?" --model gemini-3.7-flash

# The same thing with no API key: broad sweep, template-rendered report.
uv run python -m kpi_engine.cli.ask --company acme-retail "what needs attention?" --persona exec --no-llm
```

Onboarding a company whose extract matches none of the shipped contracts — the
agent reads the file's columns, works out which of the documented KPIs it can
actually support, and you approve or edit the mapping before anything is written:

```bash
uv run python -m kpi_engine.cli.init_company --company orbit --display-name "Orbit" \
    --domain other --template blank            # `blank` declares no KPIs
uv run python -m kpi_engine.cli.plan_kpis    --company orbit --from-csv extract.csv
uv run python -m kpi_engine.cli.confirm_kpis --company orbit --accept-all
uv run python -m kpi_engine.cli.ask          --company orbit "what needs attention?"
```

`plan_kpis --no-llm` works with no API key and still binds most of a well-named
extract; see **Where the LLM attaches**.

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
uv run python -m kpi_engine.cli.ask --company acme-retail \
  "Net Profit Margin and Inventory Turnover fell in August and September 2026. \
Did the Kestrel Logistics supplier disruption cause it? Compare Kestrel Logistics \
against Northwind Foods and trace the path through to COGS." \
  --persona analyst --time-grain week --entity-keys Supplier

# The clean one: a West-only ad shock, with four untreated regions as donors.
# Ground truth puts the CAC split at ln(1.15) : -ln(0.80), so the report is
# checkable against an arithmetic answer.
uv run python -m kpi_engine.cli.ask --company acme-retail \
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
uv run python -m kpi_engine.cli.profile_source --company acme-retail

# 0b. Manufacture events with known causes (see "Why inject data" below)
uv run python -m kpi_engine.cli.inject_scenario --company acme-retail

# 0c. Derive the weekly supply-chain source from the sales file
uv run python -m kpi_engine.cli.generate_scm --company acme-retail

# 1-4. Detect, attribute, score
uv run python -m kpi_engine.cli.run_pipeline --company acme-retail \
    --entity-keys Region --time-grain week --evaluate
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
`user/acme-retail/configs/detection/default.yaml` was set by measuring, not by guessing.

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

**The KPI catalogue is data, not prompt.** Onboarding could have handed the model
the KPI document and asked for `measures` and an `expression` back. It does not,
because the guard against a bad formula is weaker than it looks:
`semantics/expressions.py` rejects anything that is not arithmetic over declared
aliases, but `revenue / revenue` is arithmetic over declared aliases, and it is
always 1.0. A model-authored formula is untrusted input that only *syntax* can be
checked on.

So the 19 documented KPIs are transcribed once, by hand, into
`templates/reference/kpi_catalog.yaml` — aliases, a valid expression over them,
unit, direction, materiality, guards — and the model's entire job becomes *which
column holds this alias*, a question a name-matcher cannot always answer and a
wrong answer to which costs one KPI rather than every number in a report.

Three things fell out of that shape:

- **`variants`.** The document says `Store Entrances OR Website Sessions`, which
  is a choice of column rather than arithmetic. An ordered list of variants makes
  the choice deterministic — both channels if the file separates them, otherwise
  whichever it has — and lets `COGS` prefer a directly supplied column over the
  stock-flow identity that reconstructs it.
- **`aggregation_safe`.** `Beg. Inventory + Purchases − End. Inventory` is correct
  daily and nonsense weekly: the panel aggregates measures to the grain *before*
  evaluating, so opening and closing stock get summed and count the same goods
  repeatedly. Such variants stay in the vocabulary, are shown with the reason, and
  are never auto-selected.
- **`synonyms`.** Exact matching under `normalise` binds 12 of 19 KPIs on the demo
  extract with no model at all. That is what makes `--no-llm` an answer rather
  than a stub, and it runs *first*, so the model extends a partial result instead
  of starting from nothing and cannot overturn a match that was already certain.

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
  graph.py         the state machine; stream_agent + run_agent, one execution path
  kpi_plan.py      LLM #3 + #4 (column binding, causal structure) + their validators
  onboard.py       propose / commit -- onboarding's two stage generators
src/kpi_api/       HTTP transport, computing nothing of its own
  events.py        AgentState -> typed events, one per stage as it lands
  logbus.py        the stage log forwarded per run
  app.py           routes, CORS, the worker-thread bridge
src/kpi_engine/
  contracts/   pydantic models for every config and payload
  sources/     DataSource protocol + csv/excel adapters (registry-dispatched)
  profiling/   redundancy, identities, grain coverage, freshness
  semantics/   restricted expression evaluator, aggregation, panel builder
  detection/   decompose, baseline, point, changepoint, multivariate, windowing
  causal/      dag, algebraic, did, its, dml, router
  scenarios/   ground-truth event injection
  evidence/    confidence, abstention, bundle, telemetry
  cli/         one executable per stage, plus `ask`, `plan_kpis`, `confirm_kpis`
  pipeline.py  the whole chain in one process, returning objects not files
  tenancy.py   CompanyPaths — the one seam between a config string and a path
  provisioning.py  create a company, give it data
  catalogue.py     exact column matching; the only KpiDef constructor
  onboarding.py    plan -> merge -> synthesise contract/source/DAG -> write, or roll back
user/          one folder per tenant: configs, data, outputs. `metadata.yaml` indexes them
templates/     company seed material (retail, supply-chain, minimal, blank)
  reference/kpi_list.csv      the KPI document, for humans
  reference/kpi_catalog.yaml  the same 19 KPIs with real arithmetic, for the binder
schemas/       exported JSON Schema — the LLM-facing contract surface
```

Every command takes a required `--company`; there is no shared `configs/` or
`data/` and no default tenant. See `CLAUDE.md` for the layout and `RUNBOOK.md`
for the flags.

## Swapping the data source

Write one class implementing `DataSource` (`describe` / `load` / `_max_date`),
register it, extend the `SourceSpec.type` literal. Nothing downstream changes:

```python
register_source("postgres", PostgresSource)
```

## Where the LLM attaches

At four points, all at the edges, and none of them produces a number.

Two run **once per question** — planning the analysis and writing the prose. Two
run **once per tenant**, when a company is onboarded and its KPIs have to be
worked out from its own extract rather than copied from a template:

```
extract ──► profile ──► [bind columns] ──► validate ──► plan ──► human decides
  LLM #3 (once per tenant)     │                                      │
                               │                          ┌───────────┘
                               ▼                          ▼
                    exact name matching first    [causal edges] ──► validate ──► configs
                    (outranks the model)             LLM #4              deterministic
```

**The model binds columns; it never writes a formula.** Every expression, unit,
direction and threshold lives in `templates/reference/kpi_catalog.yaml`,
transcribed by hand from the KPI document and checked into git. This has to be
structural rather than validated: the restricted AST evaluator confirms that a
formula *parses*, and `revenue / revenue` parses perfectly well.

**Exact name matching runs first and wins ties.** A synonym that equals a column
under `normalise` is settled; the model is shown that result and asked only to
place what is left, and where the two disagree the match stands with the
disagreement recorded. So `--no-llm` binds fewer KPIs, never wrong ones, and still
produces a runnable contract, a valid DAG and levers with named owners. On the
demo extract it binds 12 of 19 with no API key at all.

Measured on an extract with every column deliberately renamed — `Total Revenue` →
`Gross Turnover`, `COGS` → `Merchandise Cost`, `Cost Of Ads` → `Paid Media Outlay`
— name matching binds 0 and the model binds 8, correctly, then names
`Paid Media Outlay → Growth Marketing` as a lever. That gap is the entire case for
the model being there.

The rest of this section is the per-question pair.

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

**What the user knows, the engine can weigh without pretending to measure it.**
A question may assert something no column carries — a heatwave, a strike, a
campaign nobody logged. The planner transcribes each into an `ExogenousFactor`,
inventing nothing around it, and `exogenous.align_factors` places it against the
detected windows by date and entity arithmetic. What comes back is a
`ContextAlignment`, which says only that two things sit on top of each other in
time. It is deliberately *not* a `CrossSourceLink`: a link needs a declared path
through the causal graph before two movements may be connected, and nothing
licenses an alignment at all. So it is offered as a coincidence worth weighing and
never as a cause — the verifier rejects any `why` claim resting on the user's word
alone, and their own figures are quotable only in a sentence that attributes them
to them. A factor that lines up with nothing is *said* to line up with nothing,
because a hypothesis silently dropped reads as one considered and dismissed.

**A question that names nothing gets a survey, not a shrug.** With no KPI named —
or every KPI named, which is the same instruction phrased differently — the run
sweeps the whole contract and reports what the EDA stage found: Theil-Sen trends,
seasonality, phase breaks. That stage has always computed these and written them
to `series_profiles.json`; until now nothing read them. It matters most where
detection is silent by design. A margin falling 1.2% a week for two years contains
no anomalous week, so the old path reported the period as quiet while the margin
fell 76% end to end. The caveat travels with the finding: at `alpha=0.05` roughly
one series in twenty-five is called trending by chance, and the stage declines to
correct for that on purpose, so a single trend call is a hypothesis rather than a
result. A recommendation resting on a trend reports its confidence as *unmeasured*
rather than as zero.

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
is how all 72 agent tests run with no API key. `json_schema` sends the strict
Pydantic schema itself, including the `additionalProperties: false` that
`extra="forbid"` emits, and the reply is validated against that same model on the
way back: a fabricated field is refused twice over.

## Two sources, deliberately mismatched

`scm_weekly_v1.csv` is weekly and keyed by supplier; the sales file
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
uv run pytest tests/ -q      # 285 tests, no API key required
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
