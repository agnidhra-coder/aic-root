"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, CalendarCheck2 } from "lucide-react";
import { Header } from "@/components/Header";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { PipelineStepper } from "@/components/PipelineStepper";
import { TierBadge } from "@/components/TierBadge";
import { EvidenceCard } from "@/components/EvidenceCard";
import { ActionPlanCard } from "@/components/ActionPlanCard";
import { useAuth } from "@/lib/auth-context";
import { ApiError, getAnalysisRequest, type KpiCase } from "@/lib/api";

const narrativeTabs: { key: string; label: string }[] = [
  { key: "operational", label: "Operational" },
  { key: "strategic", label: "Strategic" },
];

function CaseDetailContent() {
  const { uploadId, caseId } = useParams<{ uploadId: string; caseId: string }>();
  const { token } = useAuth();
  const [kpiCase, setKpiCase] = useState<KpiCase | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [activeTab, setActiveTab] = useState(narrativeTabs[0].key);

  useEffect(() => {
    async function load() {
      if (!token) return;
      try {
        const result = await getAnalysisRequest(token, uploadId);
        const found = result.cases.find((c) => c.id === caseId);
        if (!found) {
          setError("This case could not be found in the upload's analysis.");
        } else {
          setKpiCase(found);
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Failed to load this case.");
      } finally {
        setIsLoading(false);
      }
    }
    void load();
  }, [token, uploadId, caseId]);

  return (
    <div className="flex min-h-screen flex-col bg-white">
      <Header />
      <main className="w-full flex-1 px-6 py-10 sm:px-10 lg:px-16 xl:px-24">
        <Link
          href="/dashboard"
          className="mb-6 inline-flex items-center gap-1.5 text-base font-medium text-slate-500 transition hover:text-accent-600"
        >
          <ArrowLeft size={17} />
          Back to dashboard
        </Link>

        {isLoading && <p className="text-base text-slate-400">Loading…</p>}

        {!isLoading && error && (
          <div className="rounded-lg bg-rose-50 px-4 py-3 text-base text-rose-600 ring-1 ring-inset ring-rose-100">
            {error}
          </div>
        )}

        {!isLoading && kpiCase && (
          <>
            <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-sm font-medium uppercase tracking-wide text-slate-400">{kpiCase.kpiName}</p>
                <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">
                  {kpiCase.detect.headline}
                </h1>
              </div>
              <TierBadge tier={kpiCase.tier} className="mt-1" />
            </div>

            <div className="mb-10 rounded-2xl border border-slate-200 bg-slate-50/60 p-8">
              <PipelineStepper active="act" size="lg" />
            </div>

            <section className="mb-10">
              <h2 className="mb-4 text-base font-semibold uppercase tracking-wide text-slate-400">1 · Detect</h2>
              <div className="rounded-xl border border-slate-200 bg-white p-6">
                <p className="text-base font-medium text-slate-900">{kpiCase.detect.headline}</p>
                <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div className="rounded-lg bg-slate-50 px-4 py-3">
                    <p className="text-sm text-slate-400">Statistical significance</p>
                    <p className="mt-0.5 text-base text-slate-700">{kpiCase.detect.statSignificance}</p>
                  </div>
                  <div className="rounded-lg bg-slate-50 px-4 py-3">
                    <p className="text-sm text-slate-400">Business-impact floor</p>
                    <p className="mt-0.5 text-base text-slate-700">{kpiCase.detect.businessImpact}</p>
                  </div>
                </div>
              </div>
            </section>

            <section className="mb-10">
              <h2 className="mb-4 text-base font-semibold uppercase tracking-wide text-slate-400">2 · Decompose</h2>
              <div className="flex flex-wrap items-center gap-3">
                {kpiCase.decompose.map((step, i) => (
                  <div key={step.dimension} className="flex items-center gap-3">
                    <div className="rounded-xl border border-slate-200 bg-white px-5 py-4">
                      <p className="text-sm text-slate-400">{step.dimension}</p>
                      <p className="text-base font-semibold text-slate-900">{step.narrowedTo}</p>
                      <p className="mt-1 text-sm text-slate-500">{step.note}</p>
                    </div>
                    {i < kpiCase.decompose.length - 1 && <span className="text-lg text-slate-300">→</span>}
                  </div>
                ))}
              </div>
            </section>

            <section className="mb-10">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-base font-semibold uppercase tracking-wide text-slate-400">
                  3 · Explain — Evidence
                </h2>
                <span className="text-base font-medium text-slate-500">
                  Combined contribution:{" "}
                  <span className="font-semibold text-slate-900">{kpiCase.contributionTotal}%</span>
                </span>
              </div>
              {kpiCase.evidence.length === 0 ? (
                <p className="rounded-xl bg-slate-50 px-6 py-5 text-base text-slate-400">
                  No evidence sources yet — this is a simulated analysis. Real evidence fusion is on the roadmap.
                </p>
              ) : (
                <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
                  {kpiCase.evidence.map((item) => (
                    <EvidenceCard key={item.label} item={item} />
                  ))}
                </div>
              )}
            </section>

            <section className="mb-10">
              <h2 className="mb-4 text-base font-semibold uppercase tracking-wide text-slate-400">
                4 · Act — Narrative
              </h2>
              <div className="rounded-xl border border-slate-200 bg-white">
                <div className="flex border-b border-slate-100 px-2 pt-2">
                  {narrativeTabs.map((tab) => (
                    <button
                      key={tab.key}
                      onClick={() => setActiveTab(tab.key)}
                      className={
                        "rounded-t-lg px-5 py-3 text-base font-medium transition " +
                        (tab.key === activeTab
                          ? "border-b-2 border-accent-500 text-accent-600"
                          : "text-slate-400 hover:text-slate-600")
                      }
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
                <div className="p-6">
                  <p className="text-lg leading-relaxed text-slate-700">
                    {kpiCase.narratives[activeTab] ?? "No narrative available."}
                  </p>
                </div>
              </div>
            </section>

            <section className="mb-10">
              <h2 className="mb-4 text-base font-semibold uppercase tracking-wide text-slate-400">
                Recommended action
              </h2>
              <ActionPlanCard action={kpiCase.action} />
            </section>

            <section className="mb-4 flex items-center gap-3 rounded-xl bg-slate-50 px-6 py-5 text-base text-slate-500">
              <CalendarCheck2 size={19} className="text-slate-400" />
              6 · Feedback & Grade — check back on {kpiCase.checkBackDate} to see if the predicted impact held.
            </section>
          </>
        )}
      </main>
      <footer className="border-t border-slate-100 py-6 text-center text-xs text-slate-400">
        Root — Team BIAI · Accenture Innovation Challenge 2026
      </footer>
    </div>
  );
}

export default function RealCaseDetailPage() {
  return (
    <ProtectedRoute>
      <CaseDetailContent />
    </ProtectedRoute>
  );
}
