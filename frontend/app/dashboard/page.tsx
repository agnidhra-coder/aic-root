"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { clsx } from "clsx";
import { Group, Panel, Separator } from "react-resizable-panels";
import { ArrowRight, Plus } from "lucide-react";
import { Header } from "@/components/Header";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { UploadsSidebar } from "@/components/UploadsSidebar";
import { UploadModal } from "@/components/UploadModal";
import { TierBadge } from "@/components/TierBadge";
import { useAuth } from "@/lib/auth-context";
import {
  ApiError,
  getAnalysisRequest,
  listUploadsRequest,
  type AnalysisResult,
  type UploadDomain,
  type UploadRecord,
} from "@/lib/api";
import { domainLabels } from "@/lib/data";

const domains: UploadDomain[] = ["retail", "supply-chain"];
const POLL_INTERVAL_MS = 3000;

function DashboardContent() {
  const { token } = useAuth();
  const router = useRouter();
  const [domain, setDomain] = useState<UploadDomain>("retail");
  const [uploads, setUploads] = useState<UploadRecord[]>([]);
  const [isLoadingUploads, setIsLoadingUploads] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [summary, setSummary] = useState<AnalysisResult | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [isLoadingSummary, setIsLoadingSummary] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);

  const loadUploads = useCallback(async () => {
    if (!token) return;
    try {
      const list = await listUploadsRequest(token);
      setUploads(list);
    } catch {
      // Ignore — the dashboard will just show whatever it last had.
    } finally {
      setIsLoadingUploads(false);
    }
  }, [token]);

  useEffect(() => {
    async function run() {
      await loadUploads();
    }
    void run();
  }, [loadUploads]);

  // Poll while anything is still pending/analyzing, so sidebar status updates live.
  useEffect(() => {
    const hasInFlight = uploads.some((u) => u.status === "pending" || u.status === "analyzing");
    if (!hasInFlight) return;

    const interval = setInterval(() => {
      void loadUploads();
    }, POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [uploads, loadUploads]);

  const domainUploads = useMemo(
    () => uploads.filter((u) => u.domain === domain).sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [uploads, domain]
  );

  const handleDomainChange = useCallback((next: UploadDomain) => {
    setDomain(next);
    setSelectedId(null);
    setSummary(null);
    setSummaryError(null);
  }, []);

  const handleSelect = useCallback(
    async (upload: UploadRecord) => {
      if (!token) return;
      setSelectedId(upload.id);
      setSummary(null);
      setSummaryError(null);
      setIsLoadingSummary(true);
      try {
        const result = await getAnalysisRequest(token, upload.id);
        setSummary(result);
      } catch (err) {
        setSummaryError(err instanceof ApiError ? err.message : "Failed to load analysis.");
      } finally {
        setIsLoadingSummary(false);
      }
    },
    [token]
  );

  const tierCounts = useMemo(() => {
    const counts = { EXPLAINED: 0, SUSPECTED: 0, UNEXPLAINED: 0 };
    for (const c of summary?.cases ?? []) counts[c.tier] += 1;
    return counts;
  }, [summary]);

  return (
    <div className="flex min-h-screen flex-col bg-white">
      <Header />
      <main className="w-full flex-1 px-6 py-10 sm:px-10 lg:px-16 xl:px-24">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">KPI Overview</h1>
            <p className="mt-1.5 text-base text-slate-500">
              Select an uploaded file to see its Detect → Decompose → Explain analysis
            </p>
          </div>
          <button
            type="button"
            onClick={() => setIsModalOpen(true)}
            className="flex items-center gap-1.5 rounded-lg bg-accent-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent-600"
          >
            <Plus size={15} />
            Upload
          </button>
        </div>

        <div className="mb-6 inline-flex rounded-lg border border-slate-200 bg-slate-50 p-1">
          {domains.map((d) => (
            <button
              key={d}
              onClick={() => handleDomainChange(d)}
              className={clsx(
                "rounded-md px-3.5 py-1.5 text-sm font-medium transition",
                domain === d ? "bg-white text-accent-600 shadow-sm" : "text-slate-500 hover:text-slate-700"
              )}
            >
              {domainLabels[d]}
            </button>
          ))}
        </div>

        <Group className="min-h-[420px] gap-4" orientation="horizontal">
          <Panel defaultSize="25" minSize="18%" maxSize="40%">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">Uploaded files</h2>
            {isLoadingUploads ? (
              <p className="text-sm text-slate-400">Loading…</p>
            ) : (
              <UploadsSidebar
                uploads={domainUploads}
                selectedId={selectedId}
                onSelect={handleSelect}
                onUploadClick={() => setIsModalOpen(true)}
              />
            )}
          </Panel>

          <Separator className="group w-3 cursor-col-resize">
            <div className="mx-auto h-full w-px bg-slate-200 transition group-hover:bg-accent-400 group-active:bg-accent-500" />
          </Separator>

          <Panel minSize="30%">
            {!selectedId && (
              <div className="flex h-full min-h-[240px] flex-col items-center justify-center gap-2 rounded-2xl bg-slate-50/60 px-6 py-16 text-center">
                <p className="text-sm text-slate-500">Select a file from the sidebar to view its analysis</p>
              </div>
            )}

            {selectedId && isLoadingSummary && (
              <div className="flex h-full min-h-[240px] items-center justify-center">
                <p className="text-base text-slate-400">Loading analysis…</p>
              </div>
            )}

            {selectedId && !isLoadingSummary && summaryError && (
              <div className="rounded-lg bg-rose-50 px-4 py-3 text-base text-rose-600 ring-1 ring-inset ring-rose-100">
                {summaryError}
              </div>
            )}

            {selectedId && !isLoadingSummary && summary && (
              <div className="rounded-2xl border border-slate-200 bg-white p-8">
                <p className="text-sm text-slate-400">
                  {summary.rowCount} rows · {summary.cases.length} KPIs found
                </p>

                <div className="mt-4 flex flex-wrap items-center gap-2.5 text-sm text-slate-500">
                  <TierBadge tier="EXPLAINED" />
                  <span>{tierCounts.EXPLAINED}</span>
                  <TierBadge tier="SUSPECTED" />
                  <span>{tierCounts.SUSPECTED}</span>
                  <TierBadge tier="UNEXPLAINED" />
                  <span>{tierCounts.UNEXPLAINED}</span>
                </div>

                {summary.cases.length > 0 && (
                  <ul className="mt-6 space-y-2.5 border-t border-slate-100 pt-6">
                    {summary.cases.slice(0, 3).map((c) => (
                      <li key={c.id} className="flex items-center justify-between text-base">
                        <span className="text-slate-600">{c.kpiName}</span>
                        <span className="font-medium text-slate-900">
                          {c.value}
                          {c.unit}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}

                <Link
                  href={`/uploads/${summary.uploadId}/analysis`}
                  className="mt-6 flex items-center justify-center gap-1.5 rounded-lg border border-slate-200 px-5 py-2.5 text-base font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
                >
                  View full analysis
                  <ArrowRight size={17} />
                </Link>
              </div>
            )}
          </Panel>
        </Group>
      </main>
      <footer className="border-t border-slate-100 py-6 text-center text-xs text-slate-400">
        Root — Team BIAI · Accenture Innovation Challenge 2026
      </footer>

      {isModalOpen && (
        <UploadModal
          initialDomain={domain}
          onClose={() => setIsModalOpen(false)}
          onUploaded={(upload) => {
            setIsModalOpen(false);
            router.push(`/uploads/${upload.id}/analysis`);
          }}
        />
      )}
    </div>
  );
}

export default function DashboardPage() {
  return (
    <ProtectedRoute>
      <DashboardContent />
    </ProtectedRoute>
  );
}
