import {
  AnalysisResult,
  ConfidenceTier,
  DriverKpi,
  KpiCase,
} from './analysis.entity';
import {
  kpisForPrimaryMetric,
  primaryMetrics,
  PrimaryMetric,
} from './kpi-knowledge-base';
import { kpiContracts, UploadDomain } from '../uploads/kpi-contracts';

const TIERS: ConfidenceTier[] = ['EXPLAINED', 'SUSPECTED', 'UNEXPLAINED'];

function parseCsv(csv: string): { headers: string[]; rowCount: number } {
  const lines = csv.split(/\r?\n/).filter((line) => line.trim().length > 0);
  if (lines.length === 0) {
    return { headers: [], rowCount: 0 };
  }
  const headers = lines[0].split(',').map((h) => h.trim());
  return { headers, rowCount: Math.max(0, lines.length - 1) };
}

function seedFromString(input: string): number {
  let hash = 0;
  for (let i = 0; i < input.length; i++) {
    hash = (hash * 31 + input.charCodeAt(i)) >>> 0;
  }
  return hash;
}

function mulberry32(seed: number) {
  let state = seed;
  return () => {
    state |= 0;
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function fabricateTrend(rand: () => number, base: number): number[] {
  const trend: number[] = [];
  let value = base;
  for (let i = 0; i < 7; i++) {
    trend.push(Math.round(value * 100) / 100);
    value += (rand() - 0.5) * base * 0.08;
  }
  return trend;
}

const PRIMARY_METRICS = new Set<string>(primaryMetrics);

function fabricateDriverBreakdown(
  kpiColumn: string,
  rand: () => number,
): DriverKpi[] {
  if (!PRIMARY_METRICS.has(kpiColumn)) return [];
  return kpisForPrimaryMetric(kpiColumn as PrimaryMetric).map((kpi) => {
    const base = 10 + rand() * 990;
    const delta = Math.round((rand() - 0.5) * 30 * 10) / 10;
    return {
      kpiName: kpi.kpiName,
      formula: kpi.formula,
      value: base.toFixed(2),
      delta,
      deltaLabel: `${delta >= 0 ? '+' : ''}${delta}% WoW`,
      keyDrivers: kpi.keyDrivers,
      impactRatio: kpi.impactRatio,
    };
  });
}

function worstDriver(drivers: DriverKpi[]): DriverKpi | null {
  if (drivers.length === 0) return null;
  return drivers.reduce((worst, d) => (d.delta < worst.delta ? d : worst));
}

function fabricateCase(
  kpiColumn: string,
  domain: string,
  rand: () => number,
  uploadId: string,
): KpiCase {
  const tier = TIERS[Math.floor(rand() * TIERS.length)];
  const base = 100 + rand() * 900;
  const delta = Math.round((rand() - 0.5) * 20 * 10) / 10;
  const trend = fabricateTrend(rand, base);
  const contributionTotal =
    tier === 'EXPLAINED'
      ? 70 + Math.round(rand() * 25)
      : tier === 'SUSPECTED'
        ? 40 + Math.round(rand() * 29)
        : Math.round(rand() * 30);

  const driverBreakdown = fabricateDriverBreakdown(kpiColumn, rand);
  const worst = worstDriver(driverBreakdown);

  const action = worst
    ? {
        driver: `${worst.kpiName} (${worst.deltaLabel}) — ${worst.keyDrivers}`,
        lever: worst.keyDrivers,
        action: `Investigate and improve ${worst.kpiName} (${worst.formula}); it moved ${worst.deltaLabel} and is the largest negative contributor to ${kpiColumn}.`,
        impact: worst.impactRatio,
        owner: 'N/A',
        confidence: tier,
        monitor: `Track ${worst.kpiName} weekly until it recovers.`,
      }
    : {
        driver: 'Simulated — pending real analysis',
        lever: 'N/A',
        action: 'N/A',
        impact: 'N/A',
        owner: 'N/A',
        confidence: tier,
        monitor: 'N/A',
      };

  return {
    id: `${uploadId}-${kpiColumn.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`,
    kpiName: kpiColumn,
    value: base.toFixed(2),
    unit: '',
    delta,
    deltaLabel: `${delta >= 0 ? '+' : ''}${delta}% WoW`,
    trend,
    tier,
    segment: 'Simulated segment',
    detect: {
      headline: `${kpiColumn} moved ${delta >= 0 ? 'up' : 'down'} ${Math.abs(delta)}% week over week.`,
      statSignificance: 'Simulated — not yet computed by the real pipeline.',
      businessImpact: 'Simulated — not yet computed by the real pipeline.',
    },
    decompose: [
      {
        dimension: 'Simulated dimension',
        narrowedTo: 'Simulated segment',
        note: 'This is placeholder output from the simulated analysis pipeline.',
      },
    ],
    driverBreakdown,
    evidence: [],
    contributionTotal,
    narratives: {
      operational: `[Simulated] ${kpiColumn} for ${domain} moved ${delta}% this week. Real evidence-backed narrative pending.`,
      strategic: `[Simulated] Trend view for ${kpiColumn} pending real analysis.`,
    },
    action,
    checkBackDate: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000)
      .toISOString()
      .slice(0, 10),
  };
}

export function runSimulatedAnalysis(params: {
  uploadId: string;
  domain: string;
  csvContent: string;
}): AnalysisResult {
  const { headers, rowCount } = parseCsv(params.csvContent);

  // Cards are always exactly the domain's contracted KPIs (the Excel's
  // Primary Metrics) — never derived from whatever columns happen to be in
  // the uploaded CSV, which may contain many raw-input columns that aren't
  // KPIs themselves (e.g. "Net Sales", "Sq Footage", "COGS").
  const contract = kpiContracts[params.domain as UploadDomain];
  const kpiColumns = contract?.kpis ?? [];

  const rand = mulberry32(seedFromString(params.uploadId));
  const cases = kpiColumns.map((col) =>
    fabricateCase(col, params.domain, rand, params.uploadId),
  );

  return {
    uploadId: params.uploadId,
    generatedAt: new Date().toISOString(),
    rowCount,
    columns: headers,
    cases,
  };
}
