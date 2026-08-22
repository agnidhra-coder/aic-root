"use client";

import { useMemo, useState } from "react";
import { clsx } from "clsx";
import type { Domain, KpiSummary } from "@/lib/types";
import { domainLabels } from "@/lib/data";
import { KpiCard } from "./KpiCard";
import { TierBadge } from "./TierBadge";

const domains: Domain[] = ["retail", "supply-chain"];

export function DashboardBoard({ kpis }: { kpis: KpiSummary[] }) {
  const [domain, setDomain] = useState<Domain>("retail");

  const filtered = useMemo(() => kpis.filter((k) => k.domain === domain), [kpis, domain]);

  const tierCounts = useMemo(() => {
    return filtered.reduce(
      (acc, k) => {
        acc[k.tier] += 1;
        return acc;
      },
      { EXPLAINED: 0, SUSPECTED: 0, UNEXPLAINED: 0 }
    );
  }, [filtered]);

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-1">
          {domains.map((d) => (
            <button
              key={d}
              onClick={() => setDomain(d)}
              className={clsx(
                "rounded-md px-3.5 py-1.5 text-sm font-medium transition",
                domain === d ? "bg-white text-accent-600 shadow-sm" : "text-slate-500 hover:text-slate-700"
              )}
            >
              {domainLabels[d]}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2 text-xs text-slate-500">
          <span>{filtered.length} KPIs</span>
          <span className="text-slate-300">·</span>
          <TierBadge tier="EXPLAINED" className="scale-90" />
          <span>{tierCounts.EXPLAINED}</span>
          <TierBadge tier="SUSPECTED" className="scale-90" />
          <span>{tierCounts.SUSPECTED}</span>
          <TierBadge tier="UNEXPLAINED" className="scale-90" />
          <span>{tierCounts.UNEXPLAINED}</span>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {filtered.map((kpi) => (
          <KpiCard key={kpi.id} kpi={kpi} />
        ))}
      </div>
    </div>
  );
}
