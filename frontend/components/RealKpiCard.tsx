"use client";

import Link from "next/link";
import { clsx } from "clsx";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import type { KpiCase } from "@/lib/api";
import { TierBadge } from "./TierBadge";
import { Sparkline } from "./Sparkline";

export function RealKpiCard({ uploadId, kpiCase }: { uploadId: string; kpiCase: KpiCase }) {
  const isPositive = kpiCase.delta >= 0;

  return (
    <div className="flex flex-col gap-5 rounded-2xl border border-accent-500 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
      <div className="flex items-start justify-between">
        <p className="text-lg font-semibold text-slate-900">{kpiCase.kpiName}</p>
        <TierBadge tier={kpiCase.tier} />
      </div>

      <div className="flex items-end justify-between">
        <div>
          <div
            className={clsx(
              "inline-flex items-center gap-1.5 text-3xl font-semibold tracking-tight",
              isPositive ? "text-emerald-600" : "text-rose-600"
            )}
          >
            {isPositive ? <ArrowUpRight size={26} /> : <ArrowDownRight size={26} />}
            {kpiCase.deltaLabel}
          </div>
          <p className="mt-1 text-sm text-slate-400">
            {kpiCase.segment}
            {kpiCase.window && ` · ${kpiCase.window}`}
          </p>
        </div>
        <Sparkline data={kpiCase.trend} positive={isPositive} width={120} height={40} />
      </div>

      <div className="flex items-center justify-between border-t border-slate-100 pt-4 text-sm text-slate-400">
        <span>Contribution {kpiCase.contributionTotal}%</span>
        <Link
          href={`/uploads/${uploadId}/cases/${kpiCase.id}`}
          className="rounded-lg bg-accent-500 px-3 py-1.5 font-medium text-white transition hover:bg-accent-600"
        >
          View analysis →
        </Link>
      </div>
    </div>
  );
}
