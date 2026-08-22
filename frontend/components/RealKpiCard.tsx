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
    <Link
      href={`/uploads/${uploadId}/cases/${kpiCase.id}`}
      className="group flex flex-col gap-5 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md hover:border-slate-300"
    >
      <div className="flex items-start justify-between">
        <div>
          <p className="text-base font-medium text-slate-500">{kpiCase.kpiName}</p>
          <p className="mt-1.5 text-3xl font-semibold tracking-tight text-slate-900">
            {kpiCase.value}
            <span className="ml-1 text-base font-normal text-slate-400">{kpiCase.unit}</span>
          </p>
        </div>
        <TierBadge tier={kpiCase.tier} />
      </div>

      <div className="flex items-end justify-between">
        <div>
          <div
            className={clsx(
              "inline-flex items-center gap-1 text-base font-medium",
              isPositive ? "text-emerald-600" : "text-rose-600"
            )}
          >
            {isPositive ? <ArrowUpRight size={17} /> : <ArrowDownRight size={17} />}
            {kpiCase.deltaLabel}
          </div>
          <p className="mt-1 text-sm text-slate-400">{kpiCase.segment}</p>
        </div>
        <Sparkline data={kpiCase.trend} positive={isPositive} width={120} height={40} />
      </div>

      <div className="flex items-center justify-between border-t border-slate-100 pt-4 text-sm text-slate-400">
        <span>Contribution {kpiCase.contributionTotal}%</span>
        <span className="font-medium text-slate-500 opacity-0 transition group-hover:opacity-100">
          View analysis →
        </span>
      </div>
    </Link>
  );
}
