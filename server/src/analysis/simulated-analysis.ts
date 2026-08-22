import { AnalysisResult, ConfidenceTier, KpiCase } from './analysis.entity';

/**
 * Stand-in for the real Detect/Decompose/Explain/Act pipeline.
 * Lightly parses the CSV (headers + row count) and fabricates a
 * plausible-looking result in the same shape the real pipeline will
 * eventually produce. Replace this file's `runSimulatedAnalysis` with
 * the real implementation when it's ready — nothing else needs to change.
 */

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
    evidence: [],
    contributionTotal,
    narratives: {
      operational: `[Simulated] ${kpiColumn} for ${domain} moved ${delta}% this week. Real evidence-backed narrative pending.`,
      strategic: `[Simulated] Trend view for ${kpiColumn} pending real analysis.`,
    },
    action: {
      driver: 'Simulated — pending real analysis',
      lever: 'N/A',
      action: 'N/A',
      impact: 'N/A',
      owner: 'N/A',
      confidence: tier,
      monitor: 'N/A',
    },
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

  const excluded = new Set([
    'date',
    'region',
    'channel',
    'dc',
    'lane',
    'carrier',
    'supplier',
    'category',
  ]);
  const kpiColumns = headers
    .filter((h) => !excluded.has(h.toLowerCase()))
    .slice(0, 5);

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
