# Analysis pipeline — guide for replacing the simulated logic

Everything upload/storage/caching/status-tracking around this is already built and
working. Your job is just to replace what happens **inside** the pipeline —
you don't need to touch anything else.

## The one function to replace

`simulated-analysis.ts` exports:

```ts
export function runSimulatedAnalysis(params: {
  uploadId: string;
  domain: string;      // 'retail' | 'supply-chain'
  csvContent: string;  // raw CSV file content, as a string
}): AnalysisResult
```

Write a real version of this (own file, own name — e.g. `real-analysis.ts`,
running actual Detect → Decompose → Explain → Act) and call it from
`analysis.service.ts` instead of `runSimulatedAnalysis`. That's the only call site.

## Where analysis is actually invoked

`analysis.service.ts`, inside `run()`:

```ts
const result = runSimulatedAnalysis(params);
```

Swap this one line. Everything before it (marking `status: 'analyzing'`,
stepping through `stage`) and after it (writing to the `analyses` table,
marking `status: 'ready'`) stays as-is.

If your real pipeline has actual distinct phases, update the `for (const stage
of STAGES)` loop above that line to reflect real progress instead of a timed
fake delay — the frontend already polls `stage` live and updates the UI
automatically, no frontend changes needed either way.

## Output shape you must return — `AnalysisResult`

Defined in `analysis.entity.ts`. This is the contract; match it exactly and
nothing downstream (API, frontend, caching) needs to change.

```ts
interface AnalysisResult {
  uploadId: string;
  generatedAt: string;   // ISO timestamp
  rowCount: number;      // rows in the CSV (excluding header)
  columns: string[];     // all column headers found
  cases: KpiCase[];       // one entry per KPI you detected/analyzed
}

interface KpiCase {
  id: string;             // must be unique within this result — used in URLs
  kpiName: string;        // e.g. "Revenue"
  value: string;          // current value, formatted for display
  unit: string;           // e.g. "%", "days", "" — appended after value
  delta: number;          // signed % change, e.g. -8 or 3.9
  deltaLabel: string;     // e.g. "-8% WoW"
  trend: number[];        // sparkline points, oldest -> newest
  tier: "EXPLAINED" | "SUSPECTED" | "UNEXPLAINED";
  segment: string;        // e.g. "South Region · Mobile"

  detect: {
    headline: string;             // one-line summary of the movement
    statSignificance: string;     // e.g. "z-score 3.2, p<0.01"
    businessImpact: string;       // e.g. "~₹6L at-risk weekly revenue"
  };

  decompose: DecomposeStep[];     // the dimension breakdown that narrowed the cause
  // DecomposeStep = { dimension: string; narrowedTo: string; note: string }

  evidence: EvidenceItem[];       // structured/unstructured/exogenous evidence
  // EvidenceItem = {
  //   kind: "structured" | "unstructured" | "exogenous";
  //   label: string; detail: string; contribution: number; // 0-100
  //   citation: string; aligned: boolean;
  // }

  contributionTotal: number;      // 0-100, sum of evidence contribution -> drives tier

  narratives: Record<string, string>;
  // must include at least these two keys — frontend renders them as tabs:
  //   { operational: "...", strategic: "..." }

  action: ActionPlan;
  // { driver, lever, action, impact, owner, confidence, monitor } — all strings

  checkBackDate: string;  // "YYYY-MM-DD"
}
```

## Rules that matter

- **`id` must be stable and unique per case within one `AnalysisResult`.**
  It's used directly in the URL `/uploads/:uploadId/cases/:caseId`. The
  simulated version does `${uploadId}-${slugified kpiName}` — keep that pattern
  or something equally deterministic.
- **Tier must reflect `contributionTotal` honestly**, per the product's own
  rule (README/business_proposal.md §2.4): EXPLAINED needs evidence that
  actually clears a high bar (≥~70%), SUSPECTED is a partial/weak case,
  UNEXPLAINED means nothing cleared the bar — don't force a guess. This tier
  is user-facing and drives the whole "honest abstention" pitch, so it can't
  just be decorative.
- **`csvContent` is the raw file exactly as uploaded.** It's already passed
  CSV structural validation (`csv-validator.ts`) before this function is ever
  called — you can assume it parses cleanly and that any column matching a
  known KPI/dimension name (see `kpi-contracts.ts`) is present and numeric
  where expected. You do NOT need to re-validate structure.
- **You know the domain contract.** `kpi-contracts.ts` has the authoritative
  list of expected KPI and dimension names per domain — use it to identify
  which columns are KPIs vs. dimensions, instead of the simulated version's
  crude exclude-list guess.
- **This function must be synchronous-feeling from the caller's side** — i.e.
  return a `Promise<AnalysisResult>` (or plain `AnalysisResult`) that
  resolves once real computation is done. It's already called from inside a
  background job, so slow/expensive work (stats, retrieval, an LLM call for
  narratives) is fine here — nothing is blocking a user-facing HTTP request.
- **Never let the LLM (if you use one for narratives/Act) touch the numbers.**
  Per the product's core rule, Detect/Decompose/Explain must stay
  deterministic/statistical; if you use an LLM anywhere, restrict it to
  narrative language in `narratives`/`action`, fed only the already-computed
  evidence — never given raw data or asked to invent a number.

## What's already handled for you (don't rebuild)

- CSV upload, storage, and structural validation (column presence, numeric
  type checks) — `uploads.service.ts` / `csv-validator.ts`
- Caching — the result is persisted once and never recomputed; opening the
  same file again just reads the stored `AnalysisResult`
- Status/stage tracking and the polling UI on the frontend
- Auth and per-user scoping of uploads

## Quick sanity check before you plug it in

Run the app, upload a CSV via `/dashboard`, and confirm:
1. `GET /uploads/:id/analysis` (after it's `ready`) returns something matching
   `AnalysisResult` exactly — the frontend will silently render garbage or
   crash on a shape mismatch, so this is worth checking directly with `curl`
   or Postman first.
2. Every `KpiCase.id` is unique and clicking into a card actually opens the
   right case at `/uploads/:uploadId/cases/:caseId`.
