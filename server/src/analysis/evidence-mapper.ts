/**
 * The adapter: `/ask`'s SSE events -> `AnalysisResult`.
 *
 * One `KpiCase` per `EventWindow` the engine actually found — not one per KPI in
 * the plan. A run that detected nothing produces an empty `cases` array and
 * leans on the extension fields (`narrative`, `trends`, `noFindings`) instead.
 *
 * The mapping is deliberately conservative: every number here comes out of a
 * `Fact`, which is the only thing the engine permits to be quoted. Nothing is
 * derived, recomputed, or rounded a second time.
 */

import {
  ActionPlan,
  AnalysisResult,
  ConfidenceTier,
  ContextAlignmentItem,
  DriverKpi,
  EvidenceItem,
  KpiCase,
  NarrativeSummary,
  TrendFinding,
  VerificationSummary,
} from './analysis.entity';
import {
  AskAbstention,
  AskEnginePayload,
  AskEventEntry,
  AskEvidencePayload,
  AskFact,
  AskIngestPayload,
  AskNarrative,
  AskNarrativePayload,
  AskNoFindingsPayload,
  KpiPlan,
} from '../python/python-api.types';

/**
 * `KpiPlan.proposed[].unit`, keyed by KPI name -- the one place a KPI's real
 * unit (currency, percent, ratio, count, days) is known. `/ask`'s own events
 * never carry it: a `Fact.unit` is only ever `"pct_delta"`, `"score"` or
 * `None`, because the fact table describes a *movement*, not the KPI's
 * underlying denomination. Built once per run and threaded through rather
 * than looked up per-number, so a KPI missing from the plan (should not
 * happen; the plan is what was confirmed) degrades to no symbol rather than
 * a lookup failure.
 */
export function unitsFromPlan(
  plan: KpiPlan | null | undefined,
): Map<string, KpiUnit> {
  const units = new Map<string, KpiUnit>();
  for (const proposal of plan?.proposed ?? []) {
    units.set(proposal.name, proposal.unit);
  }
  return units;
}

export type KpiUnit = 'currency' | 'ratio' | 'percent' | 'count' | 'days';

/**
 * The symbol/suffix for one formatted number, given its KPI's unit. `₹` per
 * the currency this deployment's data is denominated in -- this only changes
 * how a number prints, never its value, so a future multi-currency company
 * is a matter of looking up a different symbol here, not re-deriving numbers.
 */
function symbolFor(unit: KpiUnit | undefined, formatted: string): string {
  switch (unit) {
    case 'currency':
      return `₹${formatted}`;
    case 'percent':
      return `${formatted}%`;
    default:
      // ratio, count, days: no symbol is the correct symbol.
      return formatted;
  }
}

/**
 * The engine's own bar. `DetectionSpec.confidence_threshold` defaults to 0.70
 * and the README's ~70% tier boundary *is* that number — kept equal
 * deliberately, not by coincidence.
 */
const CONFIDENCE_THRESHOLD = 0.7;

/** Everything the stream told us, accumulated frame by frame. */
export interface AskAccumulator {
  ingest?: AskIngestPayload;
  engine?: AskEnginePayload;
  evidence?: AskEvidencePayload;
  narrative?: AskNarrativePayload;
  verification?: Record<string, unknown>;
  report?: { report_markdown?: string };
  noFindings?: AskNoFindingsPayload;
  clarification?: { message?: string };
  error?: { type: string; message: string };
  runId?: string;
  outcome?: string;
}

export function emptyAccumulator(): AskAccumulator {
  return {};
}

/** Fold one decoded SSE frame into the accumulator. */
export function absorbFrame(
  acc: AskAccumulator,
  event: string,
  data: Record<string, unknown>,
): void {
  switch (event) {
    case 'started':
      acc.runId = data.run_id as string;
      break;
    case 'ingest':
      acc.ingest = data as unknown as AskIngestPayload;
      break;
    case 'engine': {
      const payload = data as unknown as AskEnginePayload;
      // The three engine stages collapse to the last, which is the final word —
      // and the last (`context`) is the one carrying the alignments.
      acc.engine = { ...acc.engine, ...payload };
      break;
    }
    case 'evidence':
      acc.evidence = data;
      break;
    case 'narrative':
      // The repair loop can fire this more than once; the last is the final one.
      acc.narrative = data as unknown as AskNarrativePayload;
      break;
    case 'verification':
      acc.verification = data;
      break;
    case 'report':
      acc.report = data;
      break;
    case 'no_findings':
      acc.noFindings = data;
      break;
    case 'clarification':
      acc.clarification = data;
      break;
    case 'error':
      acc.error = data as unknown as { type: string; message: string };
      break;
    case 'done':
      acc.runId = (data.run_id as string) ?? acc.runId;
      acc.outcome = data.outcome as string;
      break;
    default:
      // `plan`, `telemetry`, `log` — the audit trail. Nothing user-facing yet.
      // TODO: surface telemetry (the deterministic-vs-model split) once the UI
      // has a place for it.
      break;
  }
}

export function buildAnalysisResult(params: {
  uploadId: string;
  question: string;
  columns: string[];
  fallbackRowCount: number;
  acc: AskAccumulator;
  kpiUnits?: Map<string, KpiUnit>;
}): AnalysisResult {
  const { acc } = params;
  const kpiUnits = params.kpiUnits ?? new Map<string, KpiUnit>();
  const facts = acc.evidence?.facts ?? [];
  const events = acc.evidence?.events ?? [];
  const factsById = new Map(facts.map((f) => [f.id, f]));

  const cases = events.map((entry) =>
    buildCase(entry, factsById, acc.evidence?.abstentions ?? [], kpiUnits),
  );

  const rowCount =
    acc.ingest?.sources?.reduce((sum, s) => sum + (s.rows ?? 0), 0) ??
    params.fallbackRowCount;

  const result: AnalysisResult = {
    uploadId: params.uploadId,
    generatedAt: new Date().toISOString(),
    rowCount,
    columns: params.columns,
    cases,
    question: params.question,
  };

  if (acc.runId) result.runId = acc.runId;
  if (acc.outcome) result.outcome = acc.outcome;

  const narrative = buildNarrative(acc.narrative);
  if (narrative) result.narrative = narrative;

  const markdown =
    acc.report?.report_markdown ?? acc.noFindings?.report_markdown ?? '';
  if (markdown) result.reportMarkdown = markdown;

  const verification = buildVerification(acc.verification);
  if (verification) result.verification = verification;

  const abstentions = acc.evidence?.abstentions ?? [];
  if (abstentions.length > 0) {
    result.abstentions = abstentions.map((a) => ({
      kpiName: a.kpi,
      reason: a.message || a.reason_code,
      whatWouldResolveIt: a.what_would_resolve_it ?? '',
    }));
  }

  const alignments = buildContextAlignments(acc.engine);
  if (alignments.length > 0) result.contextAlignments = alignments;

  const trends = buildTrends(facts);
  if (trends.length > 0) result.trends = trends;

  const caveats = acc.evidence?.data_caveats ?? [];
  if (caveats.length > 0) result.dataCaveats = caveats;

  if (acc.noFindings) {
    const searched = acc.noFindings.searched;
    const trending = Object.values(acc.noFindings.trending ?? {});
    result.noFindings = {
      timeGrain: searched?.time_grain ?? null,
      entityKeys: searched?.entity_keys ?? [],
      period: searched?.period ?? 'the full history',
      trendingCount: trending.reduce((sum, n) => sum + (n ?? 0), 0),
    };
  }

  return result;
}

// --------------------------------------------------------------------------
// One event window -> one KpiCase
// --------------------------------------------------------------------------

function buildCase(
  entry: AskEventEntry,
  factsById: Map<string, AskFact>,
  abstentions: AskAbstention[],
  kpiUnits: Map<string, KpiUnit>,
): KpiCase {
  const eventFacts = entry.fact_ids
    .map((id) => factsById.get(id))
    .filter((f): f is AskFact => Boolean(f));

  const movements = eventFacts.filter((f) => f.kind === 'movement');
  const contributions = eventFacts.filter((f) => f.kind === 'contribution');
  const methods = eventFacts.filter((f) => f.kind === 'method');
  const confidenceFact = eventFacts.find((f) => f.kind === 'confidence');

  const headlineMovement = pickHeadlineMovement(movements);
  const kpiName =
    headlineMovement?.kpi ?? entry.kpis_moved[0] ?? entry.event_id;
  const delta = headlineMovement?.value ?? 0;
  const movementText = formatMovement(
    headlineMovement,
    kpiName,
    kpiUnits.get(kpiName),
  );

  const abstention = abstentions.find((a) => a.event_id === entry.event_id);
  const tier = deriveTier(entry, abstention);

  return {
    id: entry.event_id,
    kpiName,
    value: movementText,
    // The formatted string already carries its own symbol (₹, %, or none),
    // so re-appending a unit here would double it.
    unit: '',
    delta,
    deltaLabel: formatDelta(delta, headlineMovement),
    // TODO: surface the real series once the evidence event carries a panel
    // slice. The engine emits no per-period array today, so the sparkline gets
    // the two points it can honestly draw: expected, then actual.
    trend: [],
    tier,
    segment: formatEntity(entry.entity),
    window: formatWindow(entry),
    detect: {
      headline: movementText,
      statSignificance: formatSignificance(entry, confidenceFact, methods),
      businessImpact: headlineMovement?.note ?? formatWindow(entry),
    },
    // The engine slices by entity keys rather than narrowing dimension by
    // dimension, so one step names the slice the window was found in.
    decompose: Object.entries(entry.entity).map(([dimension, narrowedTo]) => ({
      dimension,
      narrowedTo,
      note: `Detected by ${entry.detectors.join(', ') || 'the detector'}`,
    })),
    driverBreakdown: contributions.map(toDriverKpi),
    evidence: buildEvidence(contributions, abstention),
    contributionTotal: Math.round((entry.confidence ?? 0) * 100),
    narratives: {},
    action: EMPTY_ACTION,
    checkBackDate: '',
  };
}

const EMPTY_ACTION: ActionPlan = {
  driver: '',
  lever: '',
  action: '',
  impact: '',
  owner: '',
  confidence: '',
  monitor: '',
};

/** The largest material move in the window is what the case is named after. */
function pickHeadlineMovement(movements: AskFact[]): AskFact | undefined {
  return [...movements].sort(
    (a, b) => Math.abs(b.value ?? 0) - Math.abs(a.value ?? 0),
  )[0];
}

/**
 * "{kpi} {+N.N}% ({expected} expected, {actual} actual)" -- the same shape as
 * the engine's own `display`, rebuilt from the raw `expected`/`actual` numbers
 * rather than quoted verbatim, because `display` formats them with no unit
 * (the engine never knows the KPI's contract there) and so is never
 * currency-marked even when the KPI is. Falls back to `display` unchanged
 * when either the raw numbers or the KPI's unit is unavailable, so an older
 * run (or a KPI missing from the plan) degrades to what it showed before
 * rather than to a broken string.
 */
function formatMovement(
  fact: AskFact | undefined,
  kpiName: string,
  unit: KpiUnit | undefined,
): string {
  if (!fact) return `${kpiName} moved`;
  if (fact.expected == null || fact.actual == null || fact.value == null) {
    return fact.display;
  }
  const pct = `${fact.value > 0 ? '+' : ''}${fact.value.toFixed(1)}%`;
  // Expected/actual are absolute levels, never deltas -- a leading '+' next
  // to a currency symbol would misread as a change rather than a value. A
  // currency level is always comma-grouped fixed notation (nobody writes
  // "₹3.885e+6"), matching the engine's own `_fmt(..., unit="currency")`;
  // scientific notation is reserved for the non-currency case, where a raw
  // unit's true scale is genuinely unknown.
  const expected =
    unit === 'currency'
      ? `₹${formatCurrencyLevel(fact.expected)}`
      : symbolFor(unit, formatMagnitude(fact.expected, false));
  const actual =
    unit === 'currency'
      ? `₹${formatCurrencyLevel(fact.actual)}`
      : symbolFor(unit, formatMagnitude(fact.actual, false));
  return `${kpiName} ${pct} (${expected} expected, ${actual} actual)`;
}

/**
 * `EvidenceBundle.confidence` decides the tier, and an abstention overrides it:
 * the engine refusing to explain a movement is exactly `UNEXPLAINED`.
 */
function deriveTier(
  entry: AskEventEntry,
  abstention: AskAbstention | undefined,
): ConfidenceTier {
  if (abstention || entry.abstained) return 'UNEXPLAINED';
  return (entry.confidence ?? 0) >= CONFIDENCE_THRESHOLD
    ? 'EXPLAINED'
    : 'SUSPECTED';
}

function toDriverKpi(fact: AskFact): DriverKpi {
  const driver = fact.label.split(' contribution to ')[0] ?? fact.label;
  return {
    kpiName: driver,
    // A driver can contribute to more than one KPI's movement in the same
    // window (e.g. Total Expenses feeds both Net Profit Margin and Gross
    // Profit), which is why the same driver name can appear more than once in
    // one case's `driverBreakdown` -- this says which movement each row is.
    explainedKpi: fact.kpi ?? '',
    // `exact` is the whole distinction between algebra and an estimate, and it
    // must never be presented as the same thing.
    formula: fact.exact
      ? 'exact algebra'
      : `estimated by ${fact.method ?? 'a model'}`,
    // `display` already reads as a full sentence ("Total Expenses: -5.03e+05
    // (+106.2% of the move)"), so `value` and `deltaLabel` would otherwise
    // repeat it verbatim. `value` carries the number alone; `deltaLabel`
    // keeps the full sentence for the arrow-prefixed delta text.
    value: formatMagnitude(fact.value),
    delta: fact.value ?? 0,
    deltaLabel: fact.display,
    keyDrivers: fact.owner ?? '',
    impactRatio: fact.note ?? '',
  };
}

function buildEvidence(
  contributions: AskFact[],
  abstention: AskAbstention | undefined,
): EvidenceItem[] {
  const items: EvidenceItem[] = contributions.map((fact) => ({
    kind: 'structured',
    label: fact.label,
    detail: fact.note ?? '',
    // `fact.value` is the driver's raw contribution amount in the KPI's own
    // unit (e.g. -502,540 dollars of Total Expenses) -- not a percentage, and
    // rendering it as one is what produced a "-502540%" progress bar. `share`
    // is the actual proportion of the move, as a fraction; `null` means the
    // total delta was zero and the engine could not define one.
    contribution:
      fact.share == null ? null : Math.round(fact.share * 1000) / 10,
    citation: fact.id,
    // `aligned` here means "this contribution is exact", which is the one
    // qualitative flag the card has room for.
    aligned: fact.exact === true,
  }));

  if (abstention) {
    items.push({
      kind: 'structured',
      label: `No explanation for ${abstention.kpi}`,
      detail: [abstention.message, abstention.what_would_resolve_it]
        .filter(Boolean)
        .join(' '),
      // An abstention names no driver and has no share of anything, which is
      // a different thing from "this driver contributed 0%" — the latter
      // would be a real, exact finding.
      contribution: null,
      citation: abstention.reason_code,
      aligned: false,
    });
  }

  return items;
}

// --------------------------------------------------------------------------
// Top-level extensions
// --------------------------------------------------------------------------

function buildNarrative(
  payload: AskNarrativePayload | undefined,
): NarrativeSummary | undefined {
  const narrative: AskNarrative | null | undefined = payload?.narrative;
  if (!narrative) return undefined;

  return {
    headline: humaniseNumbers(narrative.headline),
    whatHappened: (narrative.what_happened ?? []).map((c) =>
      humaniseNumbers(c.text),
    ),
    why: (narrative.why ?? []).map((c) => humaniseNumbers(c.text)),
    needsAttention: (narrative.needs_attention ?? []).map((c) =>
      humaniseNumbers(c.text),
    ),
    uncertainty: humaniseNumbers(narrative.uncertainty ?? ''),
    abstainedFrom: (narrative.abstained_from ?? []).map(humaniseNumbers),
    usedFallback: Boolean(payload?.used_fallback),
  };
}

/**
 * Rounds a raw float quoted verbatim in prose down to a readable precision.
 *
 * The narrator is required to quote a number straight out of the fact table
 * rather than a fact's pre-formatted `display` string (so it writes its own
 * sentence around the figure, not around machine-labelled text) -- but the
 * table's own numbers are full floating-point precision
 * ("-4.0940706440064485"), and nothing requires the model to round what it
 * quotes. This is presentation only: it changes how a number prints, never
 * which digits are the real ones, so it runs after verification rather than
 * being something the model or the verifier has to agree on.
 */
function humaniseNumbers(text: string): string {
  return text.replace(/-?\d+\.\d{3,}/g, (match) => {
    const value = Number(match);
    if (Number.isNaN(value)) return match;
    const abs = Math.abs(value);
    // A large raw amount (dollars, units) is compact scientific notation,
    // same as `formatMagnitude` uses for a driver's own contribution value.
    if (abs !== 0 && (abs >= 1e6 || abs < 1e-3)) {
      return value.toExponential(3);
    }
    // A sub-1 figure is almost always a confidence score or a share, where
    // 2 decimal places is the precision the rest of the app already quotes
    // it at (e.g. "confidence 0.86"); a percentage-scale figure reads best
    // to 1; anything larger still gets 2.
    const decimals = abs < 1 ? 2 : abs < 10 ? 1 : 2;
    return String(Number(value.toFixed(decimals)));
  });
}

function buildVerification(
  payload: Record<string, unknown> | undefined,
): VerificationSummary | undefined {
  if (!payload) return undefined;
  const violations = Array.isArray(payload.violations)
    ? (payload.violations as { code: string; where: string; detail: string }[])
    : [];
  return { passed: Boolean(payload.passed), violations };
}

function buildContextAlignments(
  engine: AskEnginePayload | undefined,
): ContextAlignmentItem[] {
  const aligned = (engine?.context_alignments ?? []).map((raw) => {
    const a = raw;
    return {
      factorLabel: (a.factor_label as string) ?? '',
      // `ContextAlignment` carries no detail of its own — the label is what the
      // user will recognise as their own point, and the note explains the
      // placement. The unaligned side is where the transcribed sentence lives.
      detail: '',
      eventId: (a.event_id as string) ?? null,
      overlapDays: (a.overlap_days as number) ?? null,
      lagDays: (a.lag_days as number) ?? null,
      entityMatch: (a.entity_match as string) ?? null,
      directionAgrees: (a.direction_agrees as boolean | null) ?? null,
      kpisMoved: (a.kpis_moved as string[]) ?? [],
      note: (a.note as string) ?? '',
      aligned: true,
    } satisfies ContextAlignmentItem;
  });

  const unaligned = (engine?.unaligned_factors ?? []).map((raw) => {
    const f = raw;
    return {
      factorLabel: (f.label as string) ?? '',
      detail: (f.detail as string) ?? '',
      eventId: null,
      overlapDays: null,
      lagDays: null,
      entityMatch: null,
      directionAgrees: null,
      kpisMoved: [],
      note:
        'Stated by the user. Nothing in the detected windows lines up with it. ' +
        'The engine did not measure it and cannot weigh it.',
      aligned: false,
    } satisfies ContextAlignmentItem;
  });

  return [...aligned, ...unaligned];
}

function buildTrends(facts: AskFact[]): TrendFinding[] {
  return facts
    .filter((f) => f.kind === 'trend')
    .map((f) => ({
      kpiName: f.kpi ?? f.label,
      segment: formatEntity(f.entity),
      display: f.display,
      note: f.note ?? '',
    }));
}

// --------------------------------------------------------------------------
// Formatting
// --------------------------------------------------------------------------

function formatEntity(entity: Record<string, string> | undefined): string {
  const parts = Object.entries(entity ?? {}).map(([k, v]) => `${k}: ${v}`);
  return parts.length > 0 ? parts.join(' · ') : 'Network-wide';
}

function formatDelta(delta: number, fact: AskFact | undefined): string {
  if (!fact || fact.value === null || fact.value === undefined) return '—';
  // `pct_delta` facts are already a signed percentage.
  return `${delta > 0 ? '+' : ''}${delta.toFixed(1)}%`;
}

/**
 * A driver's raw contribution value alone, without the "(+N% of the move)"
 * sentence `display` also carries — `deltaLabel` shows that fuller sentence
 * already, so `value` stays the bare number to avoid printing it twice.
 */
function formatMagnitude(
  value: number | null | undefined,
  signed: boolean = true,
): string {
  if (value === null || value === undefined) return '—';
  const sign = signed && value > 0 ? '+' : '';
  const abs = Math.abs(value);
  // Match the engine's own `+,.4g`-style formatting: compact for very large
  // or very small magnitudes, otherwise a plain fixed-point number.
  if (abs !== 0 && (abs >= 1e6 || abs < 1e-3)) {
    return `${sign}${value.toExponential(3)}`;
  }
  return `${sign}${Number(value.toFixed(4))}`;
}

/**
 * Comma-grouped, two-decimal money, matching the engine's own
 * `_fmt(..., unit="currency")` -- currency never switches to scientific
 * notation the way an arbitrary raw amount might, because a reader expects
 * "₹3,885,000.00", not "₹3.885e+6".
 */
function formatCurrencyLevel(value: number): string {
  return value.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatWindow(entry: AskEventEntry): string {
  const [start, end] = entry.window ?? ['', ''];
  return start && end ? `${start} to ${end}` : '';
}

function formatSignificance(
  entry: AskEventEntry,
  confidence: AskFact | undefined,
  methods: AskFact[],
): string {
  const parts: string[] = [];
  if (confidence) parts.push(`confidence ${confidence.display}`);
  if (methods.length > 0) {
    parts.push(methods.map((m) => m.display).join(', '));
  } else if (entry.deterministic_methods.length > 0) {
    parts.push(entry.deterministic_methods.join(', '));
  }
  if (entry.anomaly_types.length > 0)
    parts.push(entry.anomaly_types.join(', '));
  return parts.join(' · ') || 'no method recorded';
}

/**
 * The first action the narrator proposed becomes the case's action plan.
 *
 * The engine produces actions per *run*, not per event, so they are attached to
 * the case whose evidence the action cites — or to the first case when it cites
 * nothing this mapping can resolve.
 */
export function attachActions(
  result: AnalysisResult,
  narrative: AskNarrative | null | undefined,
  factsById: Map<string, AskFact>,
): void {
  const actions = narrative?.actions ?? [];
  if (actions.length === 0 || result.cases.length === 0) return;

  for (const action of actions) {
    const plan: ActionPlan = {
      driver: action.driver,
      lever: action.lever,
      action: action.action,
      impact: action.expected_impact,
      owner: action.owner,
      confidence:
        action.confidence === null || action.confidence === undefined
          ? 'not grounded in a measured explanation'
          : action.confidence.toFixed(2),
      monitor: action.monitoring,
    };

    // Route it to the case whose evidence the action cites, when it cites any.
    const citedEventIds = new Set(
      action.evidence_ids
        .map((id) => factsById.get(id))
        .map((f) => f?.lineage?.event_id)
        .filter((id): id is string => typeof id === 'string'),
    );

    const target =
      result.cases.find((c) => citedEventIds.has(c.id)) ?? result.cases[0];
    // The highest-confidence action wins where several land on one case.
    if (!target.action.action) {
      target.action = plan;
    }
  }
}

/** The single narrative reaches both persona slots — one run, one narrative. */
export function attachNarratives(result: AnalysisResult): void {
  const narrative = result.narrative;
  if (!narrative) return;

  // TODO: two personas would need two `/ask` runs. One run, one narrative, so
  // both slots carry the same points rather than inventing a second voice.
  const points = [
    narrative.headline,
    ...narrative.whatHappened,
    ...narrative.why,
  ].filter(Boolean);

  for (const kpiCase of result.cases) {
    kpiCase.narratives = { operational: points, strategic: points };
  }
}
