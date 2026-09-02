import { clsx } from "clsx";
import { ArrowDownRight, ArrowUpRight, Info } from "lucide-react";
import type { DriverKpi } from "@/lib/api";
import { MethodBadge } from "./MethodBadge";

function groupByExplainedKpi(drivers: DriverKpi[]): Map<string, DriverKpi[]> {
  const groups = new Map<string, DriverKpi[]>();
  for (const driver of drivers) {
    const key = driver.explainedKpi || "This metric";
    const group = groups.get(key);
    if (group) group.push(driver);
    else groups.set(key, [driver]);
  }
  return groups;
}

function hasOffsettingDrivers(drivers: DriverKpi[]): boolean {
  const shares = drivers.map((d) => d.delta).filter((d) => d !== 0);
  const hasLargeShare = shares.some((d) => Math.abs(d) > 100);
  const signs = new Set(shares.map((d) => (d > 0 ? 1 : -1)));
  return hasLargeShare && signs.size > 1;
}

export function DriverBreakdownCard({ drivers }: { drivers: DriverKpi[] }) {
  if (drivers.length === 0) {
    return (
      <p className="rounded-xl bg-slate-50 px-6 py-5 text-base text-slate-400">
        No underlying driver KPIs are mapped for this metric yet.
      </p>
    );
  }

  const groups = [...groupByExplainedKpi(drivers)];
  const showHeadings = groups.length > 1;

  return (
    <div className="space-y-5">
      {groups.map(([explainedKpi, group]) => (
        <div key={explainedKpi} className="overflow-hidden rounded-xl border border-slate-200 bg-white">
          {showHeadings && (
            <p className="border-b border-slate-100 bg-slate-50/60 px-6 py-2.5 text-sm font-medium text-slate-500">
              Explains {explainedKpi}
            </p>
          )}
          {hasOffsettingDrivers(group) && (
            <div className="flex items-start gap-2 border-b border-amber-100 bg-amber-50/60 px-6 py-2.5 text-sm text-amber-700">
              <Info size={15} className="mt-0.5 shrink-0" />
              <p>
                These drivers moved in opposite directions and partly offset each other, which is
                why some contributions below are larger than 100% — the KPI&apos;s actual move is
                the small leftover of two much bigger swings.
              </p>
            </div>
          )}
          <DriverGroup drivers={group} />
        </div>
      ))}
    </div>
  );
}

function DriverGroup({ drivers }: { drivers: DriverKpi[] }) {
  const sorted = [...drivers].sort((a, b) => a.delta - b.delta);
  const worst = sorted[0];

  return (
    <div className="divide-y divide-slate-100">
      {sorted.map((driver, index) => {
        const isPositive = driver.delta >= 0;
        const isWorst = driver === worst;
        return (
          <div
            key={`${driver.kpiName}-${index}`}
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
              <MethodBadge exact={driver.exact} method={driver.method} className="mt-1.5" />
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
  );
}
