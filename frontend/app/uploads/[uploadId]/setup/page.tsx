"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Loader2, XCircle } from "lucide-react";
import { Header } from "@/components/Header";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { StepIndicator } from "@/components/StepIndicator";
import { KpiPlanSelector } from "@/components/KpiPlanSelector";
import { QuestionPrompt } from "@/components/QuestionPrompt";
import { useAuth } from "@/lib/auth-context";
import {
  ApiError,
  confirmKpiPlanRequest,
  getUploadRequest,
  startAnalysisRequest,
  type UploadRecord,
} from "@/lib/api";

const POLL_INTERVAL_MS = 2000;

function SetupContent() {
  const { uploadId } = useParams<{ uploadId: string }>();
  const router = useRouter();
  const { token } = useAuth();
  const [upload, setUpload] = useState<UploadRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const load = useCallback(async () => {
    if (!token) return null;
    const current = await getUploadRequest(token, uploadId);
    setUpload(current);
    return current;
  }, [token, uploadId]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;

    async function poll() {
      try {
        const current = await load();
        if (cancelled || !current) return;

        if (current.status === "ready" || current.status === "analyzing") {
          router.replace(`/uploads/${uploadId}/analysis`);
        } else if (current.status === "failed") {
          setError(
            current.error_message ?? "We could not read this file. Please try uploading it again."
          );
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Failed to load this upload.");
        }
      }
    }

    void poll();
    const interval = setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [token, uploadId, load, router]);

  const handleConfirmPlan = useCallback(
    async (acceptedKpis: string[]) => {
      if (!token) return;
      setIsSubmitting(true);
      setActionError(null);
      try {
        const updated = await confirmKpiPlanRequest(token, uploadId, acceptedKpis);
        setUpload(updated);
      } catch (err) {
        setActionError(
          err instanceof ApiError ? err.message : "Failed to confirm your KPI selection."
        );
      } finally {
        setIsSubmitting(false);
      }
    },
    [token, uploadId]
  );

  const handleSubmitQuestion = useCallback(
    async (question: string) => {
      if (!token) return;
      setIsSubmitting(true);
      setActionError(null);
      try {
        await startAnalysisRequest(token, uploadId, question);
        router.push(`/uploads/${uploadId}/analysis`);
      } catch (err) {
        setActionError(
          err instanceof ApiError ? err.message : "Failed to start the analysis."
        );
        setIsSubmitting(false);
      }
    },
    [token, uploadId, router]
  );

  const isPlanning = upload?.status === "planning" || upload?.status === "pending";
  const isConfirming = upload?.status === "confirming";
  const step = upload?.status === "awaiting_question" ? 2 : 1;

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

        {upload && (
          <p className="mb-6 truncate text-sm text-slate-400">{upload.filename}</p>
        )}

        {error && (
          <div className="flex items-start gap-3 rounded-xl border border-rose-100 bg-rose-50 px-5 py-4 text-sm text-rose-600">
            <XCircle size={18} className="mt-0.5 shrink-0" />
            <p className="font-medium">{error}</p>
          </div>
        )}

        {!error && (!upload || isPlanning || isConfirming) && (
          <div className="flex flex-col items-center gap-4 py-24 text-center">
            <Loader2 size={26} className="animate-spin text-slate-400" />
            <p className="text-xl font-semibold text-slate-700">
              {isConfirming
                ? "Setting up your KPIs…"
                : upload
                  ? "Reading your columns…"
                  : "Loading upload…"}
            </p>
            <p className="max-w-md text-base text-slate-400">
              {isConfirming
                ? "Writing your workspace configuration and warming up the pipeline."
                : "We are profiling every column to work out which KPIs your file can support. On a wide file this takes a minute."}
            </p>
          </div>
        )}

        {!error && upload?.status === "awaiting_plan" && upload.kpi_plan && (
          <>
            <StepIndicator currentStep={step} totalSteps={2} />
            <KpiPlanSelector
              plan={upload.kpi_plan}
              isSubmitting={isSubmitting}
              error={actionError}
              onConfirm={handleConfirmPlan}
            />
          </>
        )}

        {!error && upload?.status === "awaiting_question" && (
          <>
            <StepIndicator currentStep={step} totalSteps={2} />
            <QuestionPrompt
              isSubmitting={isSubmitting}
              error={actionError}
              onSubmit={handleSubmitQuestion}
            />
          </>
        )}
      </main>
      <footer className="border-t border-slate-100 py-6 text-center text-xs text-slate-400">
        Root — Team BIAI · Accenture Innovation Challenge 2026
      </footer>
    </div>
  );
}

export default function SetupPage() {
  return (
    <ProtectedRoute>
      <SetupContent />
    </ProtectedRoute>
  );
}
