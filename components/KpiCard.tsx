"use client";

import Link from "next/link";
import type { KpiSummary } from "@/lib/types";
import { TierBadge } from "./TierBadge";
import { Sparkline } from "./Sparkline";
import { clsx } from "clsx";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";

export function KpiCard({ kpi }: { kpi: KpiSummary }) {
  const isPositive = kpi.delta >= 0;
  const goodDirection = kpi.id.includes("returns") || kpi.id.includes("stockout") || kpi.id.includes("cost") || kpi.id.includes("leadtime")
    ? !isPositive
    : isPositive;

  return (
    <Link
      href={`/cases/${kpi.id}`}
      className="group flex flex-col gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md hover:border-slate-300"
    >
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm font-medium text-slate-500">{kpi.name}</p>
          <p className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">
            {kpi.value}
            <span className="ml-1 text-sm font-normal text-slate-400">{kpi.unit}</span>
          </p>
        </div>
        <TierBadge tier={kpi.tier} />
      </div>

      <div className="flex items-end justify-between">
        <div>
          <div
            className={clsx(
              "inline-flex items-center gap-1 text-sm font-medium",
              goodDirection ? "text-emerald-600" : "text-rose-600"
            )}
          >
            {isPositive ? <ArrowUpRight size={15} /> : <ArrowDownRight size={15} />}
            {kpi.deltaLabel}
          </div>
          <p className="mt-1 text-xs text-slate-400">{kpi.segment}</p>
        </div>
        <Sparkline data={kpi.trend} positive={goodDirection} />
      </div>

      <div className="flex items-center justify-between border-t border-slate-100 pt-3 text-xs text-slate-400">
        <span>Updated {kpi.updatedAt}</span>
        <span className="font-medium text-slate-500 opacity-0 transition group-hover:opacity-100">
          View analysis →
        </span>
      </div>
    </Link>
  );
}
