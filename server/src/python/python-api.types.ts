/**
 * Mirrors of the Pydantic models in `llm_backend/src/kpi_api/models.py` and
 * `llm_backend/src/kpi_engine/contracts/onboarding.py`.
 *
 * These request models use `extra="forbid"` on the Python side, so every field
 * name here must match exactly and nothing extra may be sent.
 */

export type PythonDomain =
  'retail' | 'supply-chain' | 'finance' | 'marketing' | 'other';

export type TimeGrain = 'day' | 'week' | 'month';

export type BindingSource = 'synonym' | 'llm' | 'user' | 'unbound';

/**
 * Something the user said happened, which the data does not contain --
 * transcribed by the planner, never measured. Crosses the wire in three
 * places: `plan.intent.exogenous`, `no_findings.intent.exogenous`, and
 * `evidence.exogenous` (a flat list of these, independent of any intent).
 */
export interface AskExogenousFactor {
  label: string;
  detail: string;
  date_start: string | null;
  date_end: string | null;
  entity_key: string | null;
  entity_value: string | null;
  affects_kpis: string[];
  expected_direction: 'increase' | 'decrease' | 'unknown';
}

/**
 * The resolved (or proposed) run configuration. Crosses the wire in
 * `plan.intent` (twice: `stage: "proposed"` then `"resolved"`) and
 * `no_findings.intent`.
 */
export interface AskAnalysisIntent {
  kpis: string[];
  entity_keys: string[];
  time_grain: TimeGrain;
  date_start: string | null;
  date_end: string | null;
  sources: string[];
  persona: string;
  question_restated: string;
  reasoning: string;
  clarification_needed: string | null;
  exogenous: AskExogenousFactor[];
}

/**
 * A user-asserted factor whose window happens to sit on a detected event --
 * a coincidence in time, never a cause. Not a `CrossSourceLink`: nothing
 * licenses this the way a declared DAG path licenses a link.
 */
export interface AskContextAlignment {
  factor_label: string;
  event_id: string;
  source_id: string | null;
  overlap_days: number;
  lag_days: number;
  entity_match: 'exact' | 'unscoped';
  kpis_moved: string[];
  direction_agrees: boolean | null;
  note: string;
}

/** A sales-side and supply-side event the causal DAG licenses connecting. */
export interface AskCrossSourceLink {
  sales_event_id: string;
  scm_event_id: string;
  shared_entity: Record<string, string>;
  sales_window: [string, string];
  scm_window: [string, string];
  overlap_days: number;
  lag_days: number;
  scm_kpis_moved: string[];
  sales_kpis_moved: string[];
  dag_path: string[];
  dag_edge: [string, string];
  relation: string;
  note: string;
}

export interface BoundMeasure {
  alias: string;
  column: string | null;
  agg: string;
  bound_by: BindingSource;
  note: string;
}

export interface KpiProposal {
  name: string;
  variant_id: string;
  category: string;
  unit: 'currency' | 'ratio' | 'percent' | 'count' | 'days';
  direction: string;
  description: string;
  expression: string;
  doc_formula: string;
  drivers: string[];
  primary_metric: string;
  measures: BoundMeasure[];
  materiality: Record<string, unknown>;
  guards: Record<string, unknown>;
  computable: boolean;
  aggregation_safe: boolean;
  recommended: boolean;
  caveats: string[];
  alternatives: string[];
}

export interface UnavailableKpi {
  name: string;
  doc_formula: string;
  missing_aliases: string[];
  tried_synonyms: string[];
  reason: string;
}

export interface ColumnReport {
  name: string;
  dtype: string;
  numeric: boolean;
  nulls: number;
  distinct: number | null;
  role: 'date' | 'entity' | 'measure' | 'unused' | 'redundant';
  used_by: string[];
  redundant_because: string;
}

export interface KpiPlan {
  schema_version: string;
  plan_id: string;
  company_id: string;
  source_id: string;
  catalog_id: string;
  generated_at: string;
  generated_by: 'llm' | 'deterministic';
  model: string | null;
  staged_csv: string;
  header: string[];
  n_rows: number;
  date_column: string | null;
  entity_columns: string[];
  time_grain: TimeGrain;
  proposed: KpiProposal[];
  unavailable: UnavailableKpi[];
  columns: ColumnReport[];
  problems: string[];
}

/** `PlanDecision` — one verdict on one proposed KPI. */
export interface PlanDecision {
  name: string;
  verdict: 'accept' | 'reject';
  variant_id?: string | null;
  bindings?: Record<string, string> | null;
}

/**
 * What `POST /companies` and `GET /company` report (cli.list_companies.describe).
 *
 * `describe()` never raises: an unopenable company comes back as
 * `status: "broken"` with only `company_id`, `status` and `problems` set,
 * which is why every field but those three is optional.
 */
export interface CompanyDescription {
  company_id: string;
  display_name?: string;
  domains?: string[];
  created_at?: string;
  // `data_ready` never existed on the Python side -- `status` is the real
  // signal. Do not reintroduce a `data_ready` field; nothing sends one.
  status?: 'ready' | 'awaiting_data' | 'broken';
  awaiting?: Record<string, string>;
  problems?: string[];
  sources?: {
    source_id: string;
    role: string;
    domain: string;
    label: string;
  }[];
  agent?: Record<string, unknown>;
  [key: string]: unknown;
}

/**
 * `collapse()`d `/kpi-plan/sync` response: one key per event name, from
 * `onboard.propose`.
 */
export interface PlanSyncResponse {
  started?: {
    plan_id: string;
    company: string;
    source_id: string;
    model: string | null;
    no_llm: boolean;
  };
  // The upload as staged: what `attach_source_data`-style checks found wrong
  // with the file itself, before any KPI is even considered.
  staged?: {
    source_id: string;
    rows: number;
    columns: number;
    header: string[];
    warnings: string[];
  };
  // Column redundancy and grain-coverage sufficiency -- the substance behind
  // a plan's caveats (why a column can't be an independent driver, whether a
  // slicing dimension has enough rows per cell). The slow step: profiling
  // searches signed combinations of up to three columns.
  profile?: {
    n_rows: number;
    redundant_columns: Record<string, string>;
    duplicate_groups: string[][];
    coverage: {
      keys: string[];
      mean_rows_per_cell: number;
      sufficient: boolean;
    }[];
  };
  plan?: { stage: 'matched' | 'resolved'; plan: KpiPlan };
  // What the model added beyond exact-name matching, and which columns it
  // could not place anywhere.
  bindings?: {
    added: string[];
    unmatched_columns: string[];
    problems: string[];
  };
  drafted?: {
    plan_id: string;
    proposed: string[];
    recommended: string[];
    unavailable: string[];
    problems: string[];
    llm_tokens?: { in: number; out: number; calls: number };
  };
  error?: { type: string; message: string };
  done?: { plan_id: string; outcome: string; duration_ms: number };
}

/**
 * `collapse()`d `/kpi-plan/confirm/sync` response: one key per event name,
 * from `onboard.commit`.
 */
export interface ConfirmSyncResponse {
  started?: {
    plan_id: string;
    run_id: string;
    company: string;
    model: string | null;
    no_llm: boolean;
    warm_up: boolean;
  };
  // The point of no return: the tenant's configs are written from here on,
  // and every later failure is about the data, not the decision.
  configs?: {
    contract_id: string;
    graph_id: string;
    kpis: string[];
    entity_columns: string[];
    date_column: string | null;
    time_grain: TimeGrain;
    edges: { deterministic: number; causal: number };
    levers: { node: string; owner: string | null }[];
    written: string[];
    superseded: string;
    problems: string[];
  };
  // Present only when a model proposed causal edges beyond the deterministic
  // ones the contract's own arithmetic already implies.
  graph?: {
    edges_proposed?: number;
    edges_added: number;
    levers?: { node: string; owner: string | null }[];
    problems: string[];
  };
  data?: {
    source_id: string;
    rows: number;
    ready: boolean;
    warnings: string[];
  };
  engine?: Record<string, unknown>;
  error?: { type: string; message: string };
  done?: { run_id: string; plan_id: string; outcome: string };
}

// --------------------------------------------------------------------------
// `/ask` stream payloads. Only the parts the adapter reads are declared; the
// engine emits considerably more.
// --------------------------------------------------------------------------

export interface AskIngestPayload {
  sources: { source_id: string; rows: number; columns: number }[];
  coverage_days?: number | null;
  min_train_periods?: number | null;
}

export interface AskFact {
  id: string;
  label: string;
  value: number | null;
  display: string;
  unit: string | null;
  kind: string;
  kpi: string | null;
  entity: Record<string, string>;
  source_id: string | null;
  method: string | null;
  exact: boolean | null;
  controllable: boolean | null;
  owner: string | null;
  lineage: Record<string, unknown>;
  note: string | null;
  // A `kind: "contribution"` fact's share of its KPI's total move, as a
  // fraction (0.253 means 25.3%). `null` when the KPI's total delta was zero
  // and the engine could not define a proportion. Present only on
  // contribution facts; absent (undefined) on every other kind.
  share?: number | null;
  // A `kind: "movement"` fact's baseline expectation and the real observed
  // value, both in the KPI's own unmarked unit -- present only on movement
  // facts; absent (undefined) on every other kind.
  expected?: number | null;
  actual?: number | null;
}

export interface AskEventEntry {
  event_id: string;
  source_id: string | null;
  window: [string, string];
  entity: Record<string, string>;
  anomaly_types: string[];
  detectors: string[];
  kpis_moved: string[];
  abstained: boolean;
  confidence: number;
  deterministic_methods: string[];
  fact_ids: string[];
}

export interface AskAbstention {
  event_id: string;
  kpi: string;
  reason_code: string;
  message: string;
  missing_evidence: string[];
  // A list, not a sentence -- `AbstainPayload.what_would_resolve_it` is
  // `list[str]` on the Python side, one item per thing that would resolve it.
  what_would_resolve_it: string[];
}

export interface AskFreshness {
  source_id: string;
  max_date: string | null;
  as_of: string;
  lag_days: number | null;
  refresh_cadence: string;
  is_stale: boolean;
}

export interface AskAbstentionSummary {
  kpi: string;
  reason_code: string;
  events: number;
  message: string;
}

export interface AskEvidencePayload {
  facts?: AskFact[];
  events?: AskEventEntry[];
  links?: AskCrossSourceLink[];
  exogenous?: AskExogenousFactor[];
  alignments?: AskContextAlignment[];
  abstentions?: AskAbstention[];
  abstention_summary?: AskAbstentionSummary[];
  freshness?: AskFreshness[];
  data_caveats?: string[];
  levers?: {
    lever: string;
    owner: string;
    column?: string;
    description?: string;
  }[];
  persona?: string;
  question_restated?: string;
  time_grain?: string;
  entity_keys?: string[];
  period_start?: string | null;
  period_end?: string | null;
}

export interface AskClaim {
  text: string;
  evidence_ids: string[];
}

export interface AskAction {
  driver: string;
  lever: string;
  action: string;
  owner: string;
  expected_impact: string;
  confidence: number | null;
  monitoring: string;
  evidence_ids: string[];
}

export interface AskGeneralRecommendation {
  related_kpi: string;
  action: string;
  rationale: string;
}

export interface AskNarrative {
  headline: string;
  what_happened: AskClaim[];
  why: AskClaim[];
  needs_attention: AskClaim[];
  actions: AskAction[];
  // General business-practice suggestions from the model's own knowledge, not
  // this tenant's measured evidence -- never grounded, never cited, never a
  // cause. Kept structurally separate from `actions` for exactly that reason.
  general_recommendations?: AskGeneralRecommendation[];
  uncertainty: string;
  abstained_from: string[];
}

export interface AskNarrativePayload {
  attempt: number;
  narrative: AskNarrative | null;
  used_fallback: boolean;
  fallback_reason: string;
}

export interface AskPerSource {
  source_id: string;
  time_grain: string;
  kpis: number;
  flags: number;
  events: number;
  explained: number;
}

export interface AskEnginePayload {
  stage: 'pipelines' | 'links' | 'context';
  per_source?: AskPerSource[];
  links?: AskCrossSourceLink[];
  context_alignments?: AskContextAlignment[];
  unaligned_factors?: AskExogenousFactor[];
  errors?: string[];
}

export interface AskNoFindingsPayload {
  searched?: {
    time_grain: string | null;
    entity_keys: string[];
    period: string;
    sources: string[];
  };
  survey?: boolean;
  trending?: Record<string, number>;
  // The resolved plan the (empty) sweep ran with -- a no-findings run that
  // narrowed the window or the KPI list is a different thing from one that
  // genuinely swept everything and found nothing.
  intent?: AskAnalysisIntent | null;
  per_source?: AskPerSource[];
  // Present when the run detected nothing *because something failed*, not
  // because the period was genuinely quiet -- the two must not read alike.
  errors?: string[];
  report_markdown?: string;
}

export interface AskDonePayload {
  run_id: string;
  outcome: 'report' | 'clarification' | 'no_findings' | 'error' | 'incomplete';
  duration_ms: number;
}

/**
 * The `AnalysisIntent`, twice: `stage: "proposed"` (the planner's first
 * answer) then `"resolved"` (after validation). `problems` names what the
 * validator adjusted -- a grain the data could not train on stepped down, an
 * override that does not exist in the catalog dropped -- never a silent
 * substitution.
 */
export interface AskPlanPayload {
  stage: 'proposed' | 'resolved';
  intent: AskAnalysisIntent | null;
  problems: string[];
}

/** One decoded SSE frame. */
export interface SseFrame {
  event: string;
  data: Record<string, unknown>;
}
