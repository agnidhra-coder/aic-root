import { clsx } from "clsx";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import type { DriverKpi } from "@/lib/api";

/**
 * One event window can bundle movements in several KPIs that share drivers —
 * `Total Expenses` and `COGS` commonly explain both Net Profit Margin and
 * Gross Profit at once — so the same driver name can legitimately appear more
 * than once here, each row explaining a different KPI's movement. Grouping by
 * `explainedKpi` is what makes that readable instead of looking like a
 * duplicate.
 */
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

export function DriverBreakdownCard({ drivers }: { drivers: DriverKpi[] }) {
  if (drivers.length === 0) {
    return (
      <p className="rounded-xl bg-slate-50 px-6 py-5 text-base text-slate-400">
        No underlying driver KPIs are mapped for this metric yet.
      </p>
    );
  }

  const groups = [...groupByExplainedKpi(drivers)];
  // A single group naming the page's own KPI needs no extra heading — the
  // page's own "Driver KPIs — what's moving X" title already says it.
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
        // Reference equality, not name equality: two distinct contribution
        // facts can legitimately name the same driver KPI, so only the
        // biggest-drag entry itself should be flagged, not every row that
        // shares its name.
        const isWorst = driver === worst;
        return (
          <div
            // `kpiName` is not unique within a group either — the same driver
            // can appear more than once via different attribution paths — so
            // the index disambiguates.
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
  );
}
