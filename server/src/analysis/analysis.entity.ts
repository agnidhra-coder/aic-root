export type ConfidenceTier = 'EXPLAINED' | 'SUSPECTED' | 'UNEXPLAINED';
export type EvidenceSourceKind = 'structured' | 'unstructured' | 'exogenous';

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

export interface AnalysisRecord {
  id: string;
  upload_id: string;
  result: AnalysisResult;
  created_at: string;
}
