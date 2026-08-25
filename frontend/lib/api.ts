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
export type UploadStatus = "pending" | "analyzing" | "ready" | "failed";
export type AnalysisStage = "detect" | "decompose" | "explain" | "act";

export interface UploadRecord {
  id: string;
  user_id: string;
  domain: UploadDomain;
  filename: string;
  storage_path: string;
  size_bytes: number;
  row_count: number | null;
  status: UploadStatus;
  stage: AnalysisStage | null;
  created_at: string;
}

export type ConfidenceTier = "EXPLAINED" | "SUSPECTED" | "UNEXPLAINED";
export type EvidenceSourceKind = "structured" | "unstructured" | "exogenous";

export interface EvidenceItem {
  kind: EvidenceSourceKind;
  label: string;
  detail: string;
  contribution: number;
  citation: string;
  aligned: boolean;
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
  formula: string;
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
  detect: {
    headline: string;
    statSignificance: string;
    businessImpact: string;
  };
  decompose: DecomposeStep[];
  driverBreakdown: DriverKpi[];
  evidence: EvidenceItem[];
  contributionTotal: number;
  narratives: Record<string, string>;
  action: ActionPlan;
  checkBackDate: string;
}

export interface AnalysisResult {
  uploadId: string;
  generatedAt: string;
  rowCount: number;
  columns: string[];
  cases: KpiCase[];
}

export function createUploadRequest(token: string, params: { domain: UploadDomain; file: File }) {
  const formData = new FormData();
  formData.append("domain", params.domain);
  formData.append("file", params.file);

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

export function getAnalysisRequest(token: string, uploadId: string) {
  return request<AnalysisResult>(`/uploads/${uploadId}/analysis`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}
