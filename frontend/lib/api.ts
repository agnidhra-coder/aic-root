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
  stagingWarnings?: string[];
  unmatchedColumns?: string[];
}

export interface UploadRecord {
  id: string;
  user_id: string;
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
  contribution: number | null;
  citation: string;
  aligned: boolean;
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
  explainedKpi: string;
  formula: string;
  exact: boolean;
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
  generalRecommendations: GeneralRecommendation[];
  action: ActionPlan;
  checkBackDate: string;
}
export interface ContextAlignmentItem {
  factorLabel: string;
  detail: string;
  eventId: string | null;
  overlapDays: number | null;
  lagDays: number | null;
entityMatch: string | null;
  directionAgrees: boolean | null;
  kpisMoved: string[];
  note: string;
  aligned: boolean;
}
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
export function getKpiPlanRequest(token: string, uploadId: string) {
  return request<KpiPlan>(`/uploads/${uploadId}/plan`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}
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
