import { clsx } from "clsx";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import type { DriverKpi } from "@/lib/api";

export function DriverBreakdownCard({ drivers }: { drivers: DriverKpi[] }) {
  if (drivers.length === 0) {
    return (
      <p className="rounded-xl bg-slate-50 px-6 py-5 text-base text-slate-400">
        No underlying driver KPIs are mapped for this metric yet.
      </p>
    );
  }

  const sorted = [...drivers].sort((a, b) => a.delta - b.delta);
  const worst = sorted[0];

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <div className="divide-y divide-slate-100">
        {sorted.map((driver) => {
          const isPositive = driver.delta >= 0;
          const isWorst = driver.kpiName === worst.kpiName;
          return (
            <div
              key={driver.kpiName}
              className={clsx(
                "flex flex-wrap items-center justify-between gap-3 px-6 py-4",
                isWorst && "bg-rose-50/60"
              )}
            >
              <div>
                <div className="flex items-center gap-2">
                  <p className="text-base font-medium text-slate-900">{driver.kpiName}</p>
                  {isWorst && (
                    <span className="rounded-full bg-rose-100 px-2 py-0.5 text-sm font-medium text-rose-700">
                      Biggest drag
                    </span>
                  )}
                </div>
                <p className="mt-0.5 font-mono text-sm text-slate-400">{driver.formula}</p>
              </div>
              <div className="flex items-center gap-4">
                <p className="text-base font-semibold text-slate-900">{driver.value}</p>
                <div
                  className={clsx(
                    "inline-flex items-center gap-1 text-base font-medium",
                    isPositive ? "text-emerald-600" : "text-rose-600"
                  )}
                >
                  {isPositive ? <ArrowUpRight size={16} /> : <ArrowDownRight size={16} />}
                  {driver.deltaLabel}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
