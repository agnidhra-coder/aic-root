import { clsx } from "clsx";
import { Database, FileText, CloudRain, Check, X } from "lucide-react";
import type { EvidenceItem } from "@/lib/types";

const iconFor = {
  structured: Database,
  unstructured: FileText,
  exogenous: CloudRain,
};

const labelFor = {
  structured: "Structured",
  unstructured: "Unstructured",
  exogenous: "Exogenous",
};

export function EvidenceCard({ item }: { item: EvidenceItem }) {
  const Icon = iconFor[item.kind];

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-slate-100 text-slate-600">
            <Icon size={14} />
          </span>
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">{labelFor[item.kind]}</p>
            <p className="text-sm font-semibold text-slate-900">{item.label}</p>
          </div>
        </div>
        <span
          className={clsx(
            "flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
            item.aligned ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"
          )}
        >
          {item.aligned ? <Check size={11} /> : <X size={11} />}
          {item.aligned ? "aligned" : "no signal"}
        </span>
      </div>

      <p className="mt-3 text-sm leading-relaxed text-slate-600">{item.detail}</p>

      <div className="mt-3">
        <div className="flex items-center justify-between text-xs text-slate-400">
          <span>Contribution</span>
          <span className="font-medium text-slate-600">{item.contribution}%</span>
        </div>
        <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
          <div
            className={clsx("h-full rounded-full", item.aligned ? "bg-accent-500" : "bg-slate-300")}
            style={{ width: `${item.contribution}%` }}
          />
        </div>
      </div>

      <p className="mt-3 truncate font-mono text-xs text-slate-400" title={item.citation}>
        {item.citation}
      </p>
    </div>
  );
}
