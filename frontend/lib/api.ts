const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:3001";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData;

  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
  });

  const body = await res.json().catch(() => null);

  if (!res.ok) {
    const message = body?.message ?? "Something went wrong. Please try again.";
    throw new ApiError(res.status, Array.isArray(message) ? message[0] : message);
  }

  return body as T;
}

export interface AuthUser {
  id: string;
  email: string;
  name: string;
}

export interface AuthResponse {
  accessToken: string;
  user: AuthUser;
}

export function registerRequest(params: { name: string; email: string; password: string }) {
  return request<AuthResponse>("/auth/register", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function loginRequest(params: { email: string; password: string }) {
  return request<AuthResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function meRequest(token: string) {
  return request<AuthUser>("/auth/me", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export type UploadDomain = "retail" | "supply-chain";

/**
 * pending -> planning -> awaiting_plan -> confirming -> awaiting_question
 * -> analyzing -> ready | failed. The middle states are the KPI-plan handshake.
 */
export type UploadStatus =
  | "pending"
  | "planning"
  | "awaiting_plan"
  | "confirming"
  | "awaiting_question"
  | "analyzing"
  | "ready"
  | "failed";

export type AnalysisStage = "detect" | "decompose" | "explain" | "act";

// --- The KPI plan, mirrored from `kpi_engine/contracts/onboarding.py`. -------

export interface BoundMeasure {
  alias: string;
  column: string | null;
  agg: string;
  bound_by: "synonym" | "llm" | "user" | "unbound";
  note: string;
}

export interface KpiProposal {
  name: string;
  variant_id: string;
  category: string;
  unit: "currency" | "ratio" | "percent" | "count" | "days";
  direction: string;
  description: string;
  expression: string;
  doc_formula: string;
  drivers: string[];
  primary_metric: string;
  measures: BoundMeasure[];
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
  role: "date" | "entity" | "measure" | "unused" | "redundant";
  used_by: string[];
  redundant_because: string;
}

export interface KpiPlan {
  plan_id: string;
  company_id: string;
  source_id: string;
  generated_by: "llm" | "deterministic";
  header: string[];
  n_rows: number;
  date_column: string | null;
  entity_columns: string[];
  time_grain: string;
  proposed: KpiProposal[];
  unavailable: UnavailableKpi[];
  columns: ColumnReport[];
  problems: string[];
  // Attached by the server, not part of the plan itself: warnings about the
  // uploaded CSV from before profiling even started, and columns the model
  // could not place against any KPI. Worth seeing before confirming.
  stagingWarnings?: string[];
  unmatchedColumns?: string[];
}

export interface UploadRecord {
  id: string;
  user_id: string;
  /**
   * Drives the dashboard tabs, and picks which analysis workspace (and
   * starting template) this upload's KPI plan runs against on the server.
   */
  domain: UploadDomain;
  filename: string;
  storage_path: string;
  size_bytes: number;
  context_filename: string | null;
  context_storage_path: string | null;
  row_count: number | null;
  status: UploadStatus;
  stage: AnalysisStage | null;
  plan_id: string | null;
  kpi_plan: KpiPlan | null;
  error_message: string | null;
  created_at: string;
}

export type ConfidenceTier = "EXPLAINED" | "SUSPECTED" | "UNEXPLAINED";
export type EvidenceSourceKind = "structured" | "unstructured" | "exogenous";

export interface EvidenceItem {
  kind: EvidenceSourceKind;
  label: string;
  detail: string;
  // The share of the KPI's total move this driver accounts for, as a whole
  // percentage (e.g. `106.2` for "+106.2%"), or `null` when the total delta
  // was zero and the engine could not define a proportion.
  contribution: number | null;
  citation: string;
  // "This contribution is exact" vs. a statistical estimate.
  aligned: boolean;
  // The statistical method's name when `aligned` is false; absent/null when
  // exact, or when the source (e.g. demo data) never set one.
  method?: string | null;
}

export interface DecomposeStep {
  dimension: string;
  narrowedTo: string;
  note: string;
}

export interface ActionPlan {
  driver: string;
  lever: string;
  action: string;
  impact: string;
  owner: string;
  confidence: string;
  monitor: string;
}

export interface DriverKpi {
  kpiName: string;
  // Which KPI this contribution explains. A driver can legitimately appear
  // more than once in one case's `driverBreakdown` when it feeds more than
  // one KPI that moved in the same window (e.g. `Total Expenses` drives both
  // Net Profit Margin and Gross Profit) -- this is what tells those rows apart.
  explainedKpi: string;
  formula: string;
  // True when this contribution follows exactly from the KPI's own formula;
  // false when it comes from a statistical method instead.
  exact: boolean;
  // The statistical method's name when `exact` is false; null when exact.
  method: string | null;
  value: string;
  delta: number;
  deltaLabel: string;
  keyDrivers: string;
  impactRatio: string;
}

export interface KpiCase {
  id: string;
  kpiName: string;
  value: string;
  unit: string;
  delta: number;
  deltaLabel: string;
  trend: number[];
  tier: ConfidenceTier;
  segment: string;
  // The period this movement was detected over -- a KPI moved *between* two
  // dates, and the magnitude alone does not say when. Formatted, human-
  // readable, or '' when the engine reported no window.
  window: string;
  detect: {
    headline: string;
    statSignificance: string;
    businessImpact: string;
  };
  decompose: DecomposeStep[];
  driverBreakdown: DriverKpi[];
  evidence: EvidenceItem[];
  contributionTotal: number;
  narratives: Record<string, string[]>;
  // General business-practice suggestions naming this case's own KPI, from
  // the model's own knowledge -- never measured, never a cause.
  generalRecommendations: GeneralRecommendation[];
  action: ActionPlan;
  checkBackDate: string;
}

/**
 * What the user asserted in their question, and what the engine did with it.
 * Its own field, never folded into `evidence`: an alignment is a coincidence in
 * time, not the causal licence a link needs a declared DAG path to earn.
 */
export interface ContextAlignmentItem {
  factorLabel: string;
  detail: string;
  eventId: string | null;
  overlapDays: number | null;
  lagDays: number | null;
  /** `"exact"` | `"unscoped"` — how the factor's entity met the event's. */
  entityMatch: string | null;
  directionAgrees: boolean | null;
  kpisMoved: string[];
  note: string;
  aligned: boolean;
}

/** A descriptive finding. A trend is described, never detected. */
export interface TrendFinding {
  kpiName: string;
  segment: string;
  display: string;
  note: string;
}

export interface GeneralRecommendation {
  relatedKpi: string;
  action: string;
  rationale: string;
}

export interface NarrativeSummary {
  headline: string;
  whatHappened: string[];
  why: string[];
  needsAttention: string[];
  // General business-practice suggestions from the model's own knowledge, not
  // measured evidence -- no numbers, no citation, never a cause.
  generalRecommendations: GeneralRecommendation[];
  uncertainty: string;
  abstainedFrom: string[];
  usedFallback: boolean;
}

export interface VerificationSummary {
  passed: boolean;
  violations: { code: string; where: string; detail: string }[];
}

export interface AnalysisResult {
  uploadId: string;
  generatedAt: string;
  rowCount: number;
  columns: string[];
  cases: KpiCase[];

  // Extensions for the real engine. All optional — history needs no backfill.
  runId?: string;
  outcome?: string;
  question?: string;
  narrative?: NarrativeSummary;
  reportMarkdown?: string;
  verification?: VerificationSummary;
  abstentions?: {
    kpiName: string;
    reason: string;
    whatWouldResolveIt: string[];
    eventCount: number;
  }[];
  contextAlignments?: ContextAlignmentItem[];
  trends?: TrendFinding[];
  dataCaveats?: string[];
  noFindings?: {
    timeGrain: string | null;
    entityKeys: string[];
    period: string;
    trendingCount: number;
  };
}

export function createUploadRequest(
  token: string,
  params: { domain: UploadDomain; file: File; contextDoc?: File | null }
) {
  const formData = new FormData();
  formData.append("domain", params.domain);
  formData.append("file", params.file);
  if (params.contextDoc) {
    formData.append("contextDoc", params.contextDoc);
  }

  return request<UploadRecord>("/uploads", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });
}

export function listUploadsRequest(token: string) {
  return request<UploadRecord[]>("/uploads", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function getUploadRequest(token: string, uploadId: string) {
  return request<UploadRecord>(`/uploads/${uploadId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function deleteUploadRequest(token: string, uploadId: string) {
  return request<void>(`/uploads/${uploadId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function getAnalysisRequest(token: string, uploadId: string) {
  return request<AnalysisResult>(`/uploads/${uploadId}/analysis`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

/** The KPI plan the engine proposed for this file. 409 until it is ready. */
export function getKpiPlanRequest(token: string, uploadId: string) {
  return request<KpiPlan>(`/uploads/${uploadId}/plan`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

/** Commit the accept/reject decisions. Everything not accepted is rejected. */
export function confirmKpiPlanRequest(
  token: string,
  uploadId: string,
  acceptedKpis: string[]
) {
  return request<UploadRecord>(`/uploads/${uploadId}/plan/confirm`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ acceptedKpis }),
  });
}

/** Ask the question and start the real analysis run. Blank is a valid ask. */
export function startAnalysisRequest(
  token: string,
  uploadId: string,
  question?: string
) {
  return request<UploadRecord>(`/uploads/${uploadId}/analysis`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify(question?.trim() ? { question: question.trim() } : {}),
  });
}
