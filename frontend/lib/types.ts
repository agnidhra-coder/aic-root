export type ConfidenceTier = "EXPLAINED" | "SUSPECTED" | "UNEXPLAINED";

export type Domain = "retail" | "supply-chain";

export type PersonaKind = "operational" | "strategic";

export interface Persona {
  id: string;
  name: string;
  kind: PersonaKind;
  scope: string;
}

export interface KpiSummary {
  id: string;
  name: string;
  domain: Domain;
  value: string;
  unit: string;
  delta: number;
  deltaLabel: string;
  trend: number[];
  tier: ConfidenceTier;
  segment: string;
  updatedAt: string;
}

export type EvidenceSourceKind = "structured" | "unstructured" | "exogenous";

export interface EvidenceItem {
  kind: EvidenceSourceKind;
  label: string;
  detail: string;
  // `null` means no share of the move is defined for this item (e.g. an
  // abstention, or a zero-delta KPI) -- distinct from a real contribution of 0%.
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

export interface RootCase {
  id: string;
  kpiId: string;
  domain: Domain;
  title: string;
  detect: {
    headline: string;
    statSignificance: string;
    businessImpact: string;
  };
  decompose: DecomposeStep[];
  evidence: EvidenceItem[];
  tier: ConfidenceTier;
  contributionTotal: number;
  narratives: Record<string, string>;
  action: ActionPlan;
  checkBackDate: string;
}
