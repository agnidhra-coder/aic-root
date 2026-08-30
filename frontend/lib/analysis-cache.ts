import type { AnalysisResult } from "./api";

/**
 * In-memory cache of finished analyses, keyed by upload id.
 *
 * An `AnalysisResult` is immutable once written — the upload it belongs to has
 * already reached `status: "ready"` and the engine never revises a past run —
 * so there is nothing to invalidate. This exists purely to skip the loading
 * flash when the user navigates back to a results page they have already
 * fetched (e.g. from a case detail page, or after leaving and returning),
 * within the same browser session. It is not persisted across reloads.
 */
const cache = new Map<string, AnalysisResult>();

export function getCachedAnalysis(uploadId: string): AnalysisResult | undefined {
  return cache.get(uploadId);
}

export function setCachedAnalysis(uploadId: string, result: AnalysisResult): void {
  cache.set(uploadId, result);
}
