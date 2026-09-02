import type { AnalysisResult } from "./api";

const cache = new Map<string, AnalysisResult>();

export function getCachedAnalysis(uploadId: string): AnalysisResult | undefined {
  return cache.get(uploadId);
}

export function setCachedAnalysis(uploadId: string, result: AnalysisResult): void {
  cache.set(uploadId, result);
}
