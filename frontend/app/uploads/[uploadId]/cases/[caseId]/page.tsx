"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, CalendarCheck2, Search, GitBranch, FileSearch, MessageSquareText } from "lucide-react";
import { clsx } from "clsx";
import { Header } from "@/components/Header";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { TierBadge } from "@/components/TierBadge";
import { EvidenceCard } from "@/components/EvidenceCard";
import { ActionPlanCard } from "@/components/ActionPlanCard";
import { DriverBreakdownCard } from "@/components/DriverBreakdownCard";
import { useAuth } from "@/lib/auth-context";
import { getCachedAnalysis, setCachedAnalysis } from "@/lib/analysis-cache";
import { ApiError, getAnalysisRequest, type KpiCase } from "@/lib/api";

const stages = [
  { key: "detect", label: "Detect", icon: Search },
  { key: "decompose", label: "Decompose", icon: GitBranch },
  { key: "explain", label: "Explain", icon: FileSearch },
  { key: "act", label: "Act", icon: MessageSquareText },
];

function StageSelector({ active, onSelect }: { active: string; onSelect: (key: string) => void }) {
  return (
    <div className="flex items-center justify-around">
      {stages.map((stage) => {
        const Icon = stage.icon;
        const isActive = stage.key === active;
        return (
          <button
            key={stage.key}
            type="button"
            onClick={() => onSelect(stage.key)}
            className="flex flex-col items-center gap-3"
          >
            <div
              className={clsx(
                "flex h-14 w-14 items-center justify-center rounded-full border-2 transition-all duration-300 ease-out",
                isActive
                  ? "scale-110 border-accent-500 bg-accent-500 text-white shadow-md shadow-accent-200"
                  : "border-accent-500 bg-white text-accent-600 hover:bg-accent-50"
              )}
            >
              <Icon size={24} />
            </div>
            <span
              className={clsx(
                "text-base font-semibold transition-colors duration-300 ease-out",
                isActive ? "text-accent-600" : "text-slate-600"
              )}
            >
              {stage.label}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function findCase(uploadId: string, caseId: string): KpiCase | undefined {
  return getCachedAnalysis(uploadId)?.cases.find((c) => c.id === caseId);
}

function CaseDetailContent() {
  const { uploadId, caseId } = useParams<{ uploadId: string; caseId: string }>();
  const { token } = useAuth();
  const [kpiCase, setKpiCase] = useState<KpiCase | null>(
    () => findCase(uploadId, caseId) ?? null
  );
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(!kpiCase);
  const [stage, setStage] = useState("detect");

  useEffect(() => {
    if (findCase(uploadId, caseId)) return;

    async function load() {
      if (!token) return;
      try {
        const result = await getAnalysisRequest(token, uploadId);
        setCachedAnalysis(uploadId, result);
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
          href={`/uploads/${uploadId}/analysis`}
          className="mb-6 inline-flex items-center gap-1.5 text-base font-medium text-slate-500 transition hover:text-accent-600"
        >
          <ArrowLeft size={17} />
          Back to results
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
                {kpiCase.window && (
                  <p className="mt-1.5 text-base text-slate-500">{kpiCase.window}</p>
                )}
              </div>
              <TierBadge tier={kpiCase.tier} className="mt-1" />
            </div>

            <div className="mb-10 rounded-2xl border border-slate-200 bg-slate-50/60 p-8">
              <StageSelector active={stage} onSelect={setStage} />
            </div>

            <div key={stage} className="animate-stage-in">
              {stage === "detect" && (
                <section className="mb-10">
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
              )}

              {stage === "decompose" && (
                <>
                  <section className="mb-10">
                    <div className="flex flex-wrap items-center gap-3">
                      {kpiCase.decompose.map((step, i) => (
                        <div key={step.dimension} className="flex items-center gap-3">
                          <div className="rounded-xl border border-slate-200 bg-white px-5 py-4">
                            <p className="text-sm text-slate-400">{step.dimension}</p>
                            <p className="text-base font-semibold text-slate-900">{step.narrowedTo}</p>
                            <p className="mt-1 text-sm text-slate-500">{step.note}</p>
                          </div>
                          {i < kpiCase.decompose.length - 1 && (
                            <span className="text-lg text-slate-300">→</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </section>

                  <section className="mb-10">
                    <h2 className="mb-4 text-base font-semibold uppercase tracking-wide text-slate-400">
                      Driver KPIs — what&apos;s moving {kpiCase.kpiName}
                    </h2>
                    <DriverBreakdownCard drivers={kpiCase.driverBreakdown} />
                  </section>
                </>
              )}

              {stage === "explain" && (
                <section className="mb-10">
                  <div className="mb-4 flex items-center justify-between">
                    <h2 className="text-base font-semibold uppercase tracking-wide text-slate-400">Evidence</h2>
                    <span className="text-base font-medium text-slate-500">
                      Combined contribution:{" "}
                      <span className="font-semibold text-slate-900">{kpiCase.contributionTotal}%</span>
                    </span>
                  </div>
                  {kpiCase.evidence.length === 0 ? (
                    <p className="rounded-xl bg-slate-50 px-6 py-5 text-base text-slate-400">
                      The engine attributed no drivers to this movement. That is an abstention, not a
                      gap: it found the move but not enough evidence to say what caused it.
                    </p>
                  ) : (
                    <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
                      {kpiCase.evidence.map((item, index) => (
                        <EvidenceCard key={`${item.label}-${index}`} item={item} />
                      ))}
                    </div>
                  )}
                </section>
              )}

              {stage === "act" && (
                <>
                  {kpiCase.action.action && (
                    <section className="mb-10">
                      <h2 className="mb-4 text-base font-semibold uppercase tracking-wide text-slate-400">
                        Recommended action
                      </h2>
                      <ActionPlanCard action={kpiCase.action} />
                    </section>
                  )}

                  <section className="mb-10">
                    <h2 className="mb-4 text-base font-semibold uppercase tracking-wide text-slate-400">
                      Narrative
                    </h2>
                    <div className="rounded-xl border border-slate-200 bg-white p-6">
                      {kpiCase.narratives.operational?.length ? (
                        <ul className="list-disc space-y-2 pl-5 text-lg leading-relaxed text-slate-700">
                          {kpiCase.narratives.operational.map((point, i) => (
                            <li key={i}>{point}</li>
                          ))}
                        </ul>
                      ) : (
                        <p className="text-lg leading-relaxed text-slate-700">No narrative available.</p>
                      )}
                    </div>
                  </section>

                  {kpiCase.generalRecommendations.length > 0 && (
                    <section className="mb-10">
                      <h2 className="mb-4 text-base font-semibold uppercase tracking-wide text-slate-400">
                        Other suggestions
                      </h2>
                      <div className="rounded-xl border border-slate-200 bg-white p-6">
                        <p className="mb-3 text-sm text-slate-400">
                          General practice for KPIs that moved this way, from the model&apos;s own
                          knowledge rather than this data. Nothing below was measured, and none of
                          it is a cause.
                        </p>
                        <ul className="list-disc space-y-2 pl-5 text-base text-slate-600">
                          {kpiCase.generalRecommendations.map((rec, i) => (
                            <li key={i}>
                              {rec.action}{" "}
                              <span className="italic text-slate-500">{rec.rationale}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    </section>
                  )}

                  {kpiCase.action.monitor && (
                    <section className="mb-4 flex items-center gap-3 rounded-xl bg-slate-50 px-6 py-5 text-base text-slate-500">
                      <CalendarCheck2 size={19} className="text-slate-400" />
                      Monitor: {kpiCase.action.monitor}
                    </section>
                  )}
                </>
              )}
            </div>
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
