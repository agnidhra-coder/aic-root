"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Loader2, XCircle } from "lucide-react";
import { Header } from "@/components/Header";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { PipelineStepper } from "@/components/PipelineStepper";
import { TierBadge } from "@/components/TierBadge";
import { RealKpiCard } from "@/components/RealKpiCard";
import { useAuth } from "@/lib/auth-context";
import { getCachedAnalysis, setCachedAnalysis } from "@/lib/analysis-cache";
import {
  ApiError,
  getAnalysisRequest,
  getUploadRequest,
  type AnalysisResult,
  type UploadRecord,
} from "@/lib/api";

const POLL_INTERVAL_MS = 2000;

/** The statuses that mean the setup steps are still outstanding. */
const AWAITING_SETUP = ["pending", "planning", "awaiting_plan", "confirming", "awaiting_question"];

function AnalysisContent() {
  const { uploadId } = useParams<{ uploadId: string }>();
  const router = useRouter();
  const { token } = useAuth();
  const [upload, setUpload] = useState<UploadRecord | null>(null);
  // Seeded synchronously from the cache so a finished analysis the user has
  // already fetched once (e.g. returning from a case detail page) renders on
  // the very first paint instead of flashing "Loading analysis…" again.
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(() =>
    getCachedAnalysis(uploadId) ?? null
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;

    async function poll() {
      if (!token) return;
      try {
        const current = await getUploadRequest(token, uploadId);
        if (cancelled) return;
        setUpload(current);

        if (current.status === "ready") {
          // Already have this run's result cached — nothing left to fetch or
          // to keep polling for.
          if (getCachedAnalysis(uploadId)) return;
          const result = await getAnalysisRequest(token, uploadId);
          if (cancelled) return;
          setCachedAnalysis(uploadId, result);
          setAnalysis(result);
        } else if (current.status === "failed") {
          setError(
            current.error_message ??
              "Analysis failed for this file. Please try uploading it again."
          );
        } else if (AWAITING_SETUP.includes(current.status)) {
          // The KPI choice or the question is still outstanding.
          router.replace(`/uploads/${uploadId}/setup`);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Failed to load this upload.");
        }
      }
    }

    void poll();
    // A cached result is already the finished report — no need to keep
    // polling an upload that can no longer change.
    if (getCachedAnalysis(uploadId)) return;
    const interval = setInterval(() => {
      void poll();
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [token, uploadId, router]);

  // A cached analysis means this run already finished — never show the
  // processing/loading state for it again, even before the upload metadata
  // fetch above has resolved.
  const isProcessing =
    !analysis &&
    upload &&
    (upload.status === "analyzing" || AWAITING_SETUP.includes(upload.status));

  return (
    <div className="flex min-h-screen flex-col bg-white">
      <Header />
      <main className="w-full flex-1 px-6 py-10 sm:px-10 lg:px-16 xl:px-24">
        <Link
          href="/dashboard"
          className="mb-6 inline-flex items-center gap-1.5 text-sm font-medium text-slate-500 transition hover:text-accent-600"
        >
          <ArrowLeft size={15} />
          Back to dashboard
        </Link>

        {error && (
          <div className="flex items-start gap-3 rounded-xl border border-rose-100 bg-rose-50 px-5 py-4 text-sm text-rose-600">
            <XCircle size={18} className="mt-0.5 shrink-0" />
            <div>
              <p className="font-medium">{error}</p>
            </div>
          </div>
        )}

        {!error && !analysis && isProcessing && (
          <div className="flex flex-col items-center gap-10 py-24 text-center">
            <div className="w-full max-w-xl">
              <PipelineStepper active={upload?.stage ?? "detect"} size="lg" />
            </div>
            <div className="flex items-center gap-3 text-slate-600">
              <Loader2 size={22} className="animate-spin" />
              <p className="text-xl font-semibold">Analyzing {upload?.filename}…</p>
            </div>
            <p className="max-w-md text-base text-slate-400">
              Root is running Detect → Decompose → Explain on your data. This usually takes a few seconds.
            </p>
          </div>
        )}

        {!error && !analysis && !isProcessing && (
          <div className="flex items-center justify-center gap-3 py-24 text-slate-400">
            <Loader2 size={18} className="animate-spin" />
            <p className="text-base">{!upload ? "Loading upload…" : "Loading analysis…"}</p>
          </div>
        )}

        {!error && analysis && (
          <>
            <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
              <div>
                <h1 className="text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">
                  {upload?.filename}
                </h1>
                <p className="mt-1.5 text-base text-slate-500">
                  {analysis.rowCount} rows · {analysis.cases.length} KPIs found
                </p>
              </div>
              <div className="flex items-center gap-2.5 text-sm text-slate-500">
                <TierBadge tier="EXPLAINED" />
                <span>{analysis.cases.filter((c) => c.tier === "EXPLAINED").length}</span>
                <TierBadge tier="SUSPECTED" />
                <span>{analysis.cases.filter((c) => c.tier === "SUSPECTED").length}</span>
                <TierBadge tier="UNEXPLAINED" />
                <span>{analysis.cases.filter((c) => c.tier === "UNEXPLAINED").length}</span>
              </div>
            </div>

            <AnalysisNarrative analysis={analysis} />

            {analysis.cases.length === 0 ? (
              <NoCases analysis={analysis} />
            ) : (
              <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3">
                {analysis.cases.map((c) => (
                  <RealKpiCard key={c.id} uploadId={analysis.uploadId} kpiCase={c} />
                ))}
              </div>
            )}

            <AnalysisFootnotes analysis={analysis} />
          </>
        )}
      </main>
      <footer className="border-t border-slate-100 py-6 text-center text-xs text-slate-400">
        Root — Team BIAI · Accenture Innovation Challenge 2026
      </footer>
    </div>
  );
}

/** The run's one narrative, in full. One run produces one, not one per persona. */
function AnalysisNarrative({ analysis }: { analysis: AnalysisResult }) {
  const narrative = analysis.narrative;
  if (!narrative) return null;

  const sections: { label: string; items: string[] }[] = [
    { label: "What happened", items: narrative.whatHappened },
    { label: "Why", items: narrative.why },
    { label: "Needs attention", items: narrative.needsAttention },
  ].filter((s) => s.items.length > 0);

  return (
    <section className="mb-5">
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3">
        {sections.map((section) => (
          <div
            key={section.label}
            className="rounded-2xl border border-slate-200 bg-white p-6"
          >
            <p className="text-base font-semibold uppercase tracking-wide text-slate-900">
              {section.label}
            </p>
            <ul className="mt-1.5 list-disc space-y-1 pl-5 text-base text-slate-600">
              {section.items.map((text, i) => (
                <li key={i}>{text}</li>
              ))}
            </ul>
            {section.label === "What happened" && (
              <p className="mt-2 text-sm text-slate-400">
                The prose above summarizes magnitude and cause; each KPI card below states the
                exact period it was detected over.
              </p>
            )}
          </div>
        ))}
      </div>

      {narrative.uncertainty && (
        <p className="mt-5 text-sm text-slate-500">
          <span className="font-medium text-slate-600">What this cannot tell you: </span>
          {narrative.uncertainty}
        </p>
      )}

      {narrative.usedFallback && (
        <p className="mt-3 text-xs text-amber-600">
          Written from the fact table directly — the model was unavailable for this run.
        </p>
      )}
    </section>
  );
}

/**
 * A run with no `KpiCase` is a real answer, not an empty one: nothing was
 * anomalous, which is different from nothing happening. Say which.
 */
function NoCases({ analysis }: { analysis: AnalysisResult }) {
  const trends = analysis.trends ?? [];

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-6">
      {analysis.noFindings ? (
        <>
          <p className="text-base font-medium text-slate-700">
            Nothing cleared the detection thresholds.
          </p>
          <p className="mt-1.5 text-base text-slate-500">
            Searched {analysis.noFindings.period} at {analysis.noFindings.timeGrain ?? "the default"}{" "}
            grain
            {analysis.noFindings.entityKeys.length > 0
              ? `, sliced by ${analysis.noFindings.entityKeys.join(", ")}`
              : ", at total level"}
            .
          </p>
        </>
      ) : (
        <p className="text-base text-slate-400">
          No KPI movements were found for the KPIs you selected.
        </p>
      )}

      {trends.length > 0 && (
        <div className="mt-5 border-t border-slate-100 pt-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Trending, without any single period being anomalous
          </p>
          <ul className="mt-2 space-y-1.5 text-base text-slate-600">
            {trends.map((trend, i) => (
              <li key={i}>
                <span className="font-medium text-slate-800">{trend.kpiName}</span>{" "}
                <span className="text-slate-400">({trend.segment})</span> — {trend.display}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** Context alignments, abstentions and caveats — the honest small print. */
function AnalysisFootnotes({ analysis }: { analysis: AnalysisResult }) {
  const alignments = analysis.contextAlignments ?? [];
  const abstentions = analysis.abstentions ?? [];
  const caveats = analysis.dataCaveats ?? [];

  if (alignments.length === 0 && abstentions.length === 0 && caveats.length === 0) {
    return null;
  }

  return (
    <section className="mt-8 space-y-6">
      {alignments.length > 0 && (
        <div className="rounded-2xl border border-slate-200 bg-white p-6">
          <h3 className="text-sm font-semibold text-slate-700">What you told us</h3>
          <p className="mt-1 text-xs text-slate-400">
            Placed against the detected windows by date and entity alone. A coincidence in time is
            not a cause, and the engine never treats it as one.
          </p>
          <ul className="mt-3 space-y-2.5">
            {alignments.map((alignment, i) => (
              <li key={i} className="text-base">
                <p className="font-medium text-slate-700">{alignment.factorLabel}</p>
                <p className="text-sm text-slate-500">
                  {alignment.aligned && alignment.eventId
                    ? `Lines up with ${alignment.eventId}${
                        alignment.overlapDays ? ` (${alignment.overlapDays} day overlap)` : ""
                      }. ${alignment.note}`
                    : alignment.note}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {abstentions.length > 0 && (
        <div className="rounded-2xl border border-slate-200 bg-white p-6">
          <h3 className="text-sm font-semibold text-slate-700">Where we could not explain</h3>
          <ul className="mt-3 space-y-2.5">
            {abstentions.map((abstention, i) => (
              <li key={i} className="text-base">
                <p className="font-medium text-slate-700">{abstention.kpiName}</p>
                <p className="text-sm text-slate-500">{abstention.reason}</p>
                {abstention.whatWouldResolveIt && (
                  <p className="text-sm text-slate-400">
                    Would resolve it: {abstention.whatWouldResolveIt}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {caveats.length > 0 && (
        <div className="rounded-2xl border border-slate-200 bg-white p-6">
          <h3 className="text-sm font-semibold text-slate-700">Data caveats</h3>
          <ul className="mt-3 list-inside list-disc space-y-1 text-sm text-slate-500">
            {caveats.map((caveat, i) => (
              <li key={i}>{caveat}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export default function AnalysisPage() {
  return (
    <ProtectedRoute>
      <AnalysisContent />
    </ProtectedRoute>
  );
}
