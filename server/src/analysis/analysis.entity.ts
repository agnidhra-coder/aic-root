export type ConfidenceTier = 'EXPLAINED' | 'SUSPECTED' | 'UNEXPLAINED';
export type EvidenceSourceKind = 'structured' | 'unstructured' | 'exogenous';

export interface EvidenceItem {
  kind: EvidenceSourceKind;
  label: string;
  detail: string;
  contribution: number | null;
  citation: string;
  aligned: boolean;
  method: string | null;
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
  planProblems?: string[];
  noFindings?: {
    timeGrain: string | null;
    entityKeys: string[];
    period: string;
    trendingCount: number;
  };
}

export interface AnalysisRecord {
  id: string;
  upload_id: string;
  result: AnalysisResult;
  created_at: string;
}
