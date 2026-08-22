import type { ActionPlan } from "@/lib/types";

const rows: { key: keyof ActionPlan; label: string }[] = [
  { key: "driver", label: "Driver" },
  { key: "lever", label: "Lever" },
  { key: "action", label: "Action" },
  { key: "impact", label: "Impact" },
  { key: "owner", label: "Owner" },
  { key: "confidence", label: "Confidence" },
  { key: "monitor", label: "Monitor" },
];

export function ActionPlanCard({ action }: { action: ActionPlan }) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <dl className="divide-y divide-slate-100">
        {rows.map(({ key, label }) => (
          <div key={key} className="grid grid-cols-1 gap-1 px-5 py-3 sm:grid-cols-[140px_1fr] sm:gap-4">
            <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</dt>
            <dd className="text-sm text-slate-700">{action[key]}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
