"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Loader2, XCircle } from "lucide-react";
import { Header } from "@/components/Header";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { PipelineStepper } from "@/components/PipelineStepper";
import { TierBadge } from "@/components/TierBadge";
import { RealKpiCard } from "@/components/RealKpiCard";
import { useAuth } from "@/lib/auth-context";
import {
  ApiError,
  getAnalysisRequest,
  getUploadRequest,
  type AnalysisResult,
  type UploadRecord,
} from "@/lib/api";

const POLL_INTERVAL_MS = 2000;

function AnalysisContent() {
  const { uploadId } = useParams<{ uploadId: string }>();
  const { token } = useAuth();
  const [upload, setUpload] = useState<UploadRecord | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
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
          const result = await getAnalysisRequest(token, uploadId);
          if (!cancelled) setAnalysis(result);
        } else if (current.status === "failed") {
          setError("Analysis failed for this file. Please try uploading it again.");
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Failed to load this upload.");
        }
      }
    }

    void poll();
    const interval = setInterval(() => {
      void poll();
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [token, uploadId]);

  const isProcessing = upload && (upload.status === "pending" || upload.status === "analyzing");

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

        {!error && (isProcessing || !upload) && (
          <div className="flex flex-col items-center gap-10 py-24 text-center">
            <div className="w-full max-w-xl">
              <PipelineStepper active={upload?.stage ?? "detect"} size="lg" />
            </div>
            <div className="flex items-center gap-3 text-slate-600">
              <Loader2 size={22} className="animate-spin" />
              <p className="text-xl font-semibold">
                {upload?.filename ? `Analyzing ${upload.filename}…` : "Loading upload…"}
              </p>
            </div>
            <p className="max-w-md text-base text-slate-400">
              Root is running Detect → Decompose → Explain on your data. This usually takes a few seconds.
            </p>
          </div>
        )}

        {!error && upload && upload.status === "ready" && !analysis && (
          <p className="py-24 text-center text-base text-slate-400">Loading analysis…</p>
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

            {analysis.cases.length === 0 ? (
              <p className="text-base text-slate-400">No KPI-like columns were found in this file.</p>
            ) : (
              <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3">
                {analysis.cases.map((c) => (
                  <RealKpiCard key={c.id} uploadId={analysis.uploadId} kpiCase={c} />
                ))}
              </div>
            )}
          </>
        )}
      </main>
      <footer className="border-t border-slate-100 py-6 text-center text-xs text-slate-400">
        Root — Team BIAI · Accenture Innovation Challenge 2026
      </footer>
    </div>
  );
}

export default function AnalysisPage() {
  return (
    <ProtectedRoute>
      <AnalysisContent />
    </ProtectedRoute>
  );
}
