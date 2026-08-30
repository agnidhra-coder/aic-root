export type ConfidenceTier = 'EXPLAINED' | 'SUSPECTED' | 'UNEXPLAINED';
export type EvidenceSourceKind = 'structured' | 'unstructured' | 'exogenous';

export interface EvidenceItem {
  kind: EvidenceSourceKind;
  label: string;
  detail: string;
  // The share of the KPI's total move this driver accounts for, as a whole
  // percentage (e.g. `106.2` for "+106.2%"), or `null` when the total delta
  // was zero and the engine could not define a proportion.
  contribution: number | null;
  citation: string;
  // "This contribution is exact" (follows directly from the KPI's formula)
  // vs. a statistical estimate. The one qualitative flag the card has room
  // for -- named `aligned` for historical reasons, not because it means
  // "lines up with something."
  aligned: boolean;
  // The statistical method's name when `aligned` is false; null when exact.
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
  // Which KPI this contribution explains. A driver can legitimately appear
  // more than once in one case's `driverBreakdown` when it feeds more than
  // one KPI that moved in the same window (e.g. `Total Expenses` drives both
  // Net Profit Margin and Gross Profit) -- this is what tells those rows apart.
  explainedKpi: string;
  formula: string;
  // True when this contribution follows exactly from the KPI's own formula
  // (e.g. Net Profit = Revenue - Costs, so each side's move is known exactly);
  // false when it comes from a statistical method instead, which carries
  // real uncertainty a plain number does not show. Never render one as if
  // it were the other.
  exact: boolean;
  // The statistical method's name (e.g. "did", "its") when `exact` is false;
  // null when `exact` is true, since there is no method to name.
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
  // readable, or '' when the engine reported no window (an abstention with
  // nothing to anchor to).
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
  // the model's own knowledge -- never measured, never a cause. Filtered from
  // the run's narrative the same way `narratives` is.
  generalRecommendations: GeneralRecommendation[];
  action: ActionPlan;
  checkBackDate: string;
}

/**
 * What the user asserted in their question, and what the engine did with it.
 * Deliberately its own field and never folded into `EvidenceItem[]` with
 * `kind: "exogenous"`: an alignment is a coincidence in time, not the causal
 * licence a cross-source link needs a declared DAG path to earn.
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

/** A descriptive `eda/` finding. A trend is described, never detected. */
export interface TrendFinding {
  kpiName: string;
  segment: string;
  display: string;
  note: string;
}

/** The single narrative the run produced, in full. */
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
  // measured evidence -- no numbers, no citation, never a cause. Kept apart
  // from the case-level `action` for exactly that reason.
  generalRecommendations: GeneralRecommendation[];
  uncertainty: string;
  abstainedFrom: string[];
  usedFallback: boolean;
}

/** The deterministic grounding check on the narrative. No model adjudicates it. */
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

  // --- Extensions for the real engine. All optional, so history needs no
  // backfill and the existing renderers keep working unchanged.

  /** The run's directory under the company's outputs/, for later artefact reads. */
  runId?: string;
  /** `report` | `clarification` | `no_findings` | `error`. */
  outcome?: string;
  /** The exact question sent to the engine. */
  question?: string;
  /** The one narrative this run produced (one persona, one run). */
  narrative?: NarrativeSummary;
  /** The markdown artefact — the checkable record of the run. */
  reportMarkdown?: string;
  /** Whether the narrative's every number was traced back to a fact. */
  verification?: VerificationSummary;
  /** Where the engine refused to answer, and what would resolve it. */
  abstentions?: {
    kpiName: string;
    reason: string;
    whatWouldResolveIt: string[];
    // How many detected events abstained for this same (KPI, reason) pair --
    // several near-identical abstentions are one finding, not several, the
    // same way the engine's own `abstention_summary` collapses them.
    eventCount: number;
  }[];
  /** The user's stated context, placed against the detected events — or not. */
  contextAlignments?: ContextAlignmentItem[];
  /** Descriptive findings, for a run where nothing cleared the thresholds. */
  trends?: TrendFinding[];
  /** Caveats the engine attached to the data itself. */
  dataCaveats?: string[];
  /**
   * What the validator adjusted about the resolved plan -- a grain the data
   * could not train on stepped down, a requested KPI or dimension dropped as
   * unknown. Never a silent substitution: if the run answered a narrower or
   * different question than the one asked, this says so.
   */
  planProblems?: string[];
  /** Populated when the run detected nothing; explains what was searched. */
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
