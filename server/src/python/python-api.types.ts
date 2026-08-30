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

/** What `POST /companies` and `GET /company` report (cli.list_companies.describe). */
export interface CompanyDescription {
  company_id: string;
  display_name?: string;
  domains?: string[];
  data_ready?: boolean;
  [key: string]: unknown;
}

/**
 * `collapse()`d `/kpi-plan/sync` response: one key per event name.
 * Only the fields we actually consume are declared.
 */
export interface PlanSyncResponse {
  started?: { plan_id: string; company: string; source_id: string };
  staged?: { rows: number; columns: number; header: string[] };
  plan?: { stage: string; plan: KpiPlan };
  drafted?: {
    plan_id: string;
    proposed: string[];
    recommended: string[];
    unavailable: string[];
    problems: string[];
  };
  error?: { type: string; message: string };
  done?: { plan_id: string; outcome: string; duration_ms: number };
}

export interface ConfirmSyncResponse {
  started?: { plan_id: string; run_id: string; company: string };
  configs?: { contract_id: string; kpis: string[]; problems: string[] };
  data?: { source_id: string; rows: number; ready: boolean };
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
  what_would_resolve_it: string;
}

export interface AskEvidencePayload {
  facts?: AskFact[];
  events?: AskEventEntry[];
  links?: Record<string, unknown>[];
  exogenous?: Record<string, unknown>[];
  alignments?: Record<string, unknown>[];
  abstentions?: AskAbstention[];
  abstention_summary?: unknown;
  data_caveats?: string[];
  levers?: { lever: string; owner: string; description?: string }[];
  persona?: string;
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

export interface AskNarrative {
  headline: string;
  what_happened: AskClaim[];
  why: AskClaim[];
  needs_attention: AskClaim[];
  actions: AskAction[];
  uncertainty: string;
  abstained_from: string[];
}

export interface AskNarrativePayload {
  attempt: number;
  narrative: AskNarrative | null;
  used_fallback: boolean;
  fallback_reason: string;
}

export interface AskEnginePayload {
  stage: 'pipelines' | 'links' | 'context';
  per_source?: unknown;
  links?: Record<string, unknown>[];
  context_alignments?: Record<string, unknown>[];
  unaligned_factors?: Record<string, unknown>[];
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
  report_markdown?: string;
}

export interface AskDonePayload {
  run_id: string;
  outcome: 'report' | 'clarification' | 'no_findings' | 'error' | 'incomplete';
  duration_ms: number;
}

/** One decoded SSE frame. */
export interface SseFrame {
  event: string;
  data: Record<string, unknown>;
}
