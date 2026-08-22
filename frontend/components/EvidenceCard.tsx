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
    <div className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-md bg-slate-100 text-slate-600">
            <Icon size={17} />
          </span>
          <div>
            <p className="text-sm font-medium uppercase tracking-wide text-slate-400">{labelFor[item.kind]}</p>
            <p className="text-base font-semibold text-slate-900">{item.label}</p>
          </div>
        </div>
        <span
          className={clsx(
            "flex items-center gap-1 rounded-full px-2.5 py-1 text-sm font-medium",
            item.aligned ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"
          )}
        >
          {item.aligned ? <Check size={13} /> : <X size={13} />}
          {item.aligned ? "aligned" : "no signal"}
        </span>
      </div>

      <p className="mt-4 text-base leading-relaxed text-slate-600">{item.detail}</p>

      <div className="mt-4">
        <div className="flex items-center justify-between text-sm text-slate-400">
          <span>Contribution</span>
          <span className="font-medium text-slate-600">{item.contribution}%</span>
        </div>
        <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-slate-100">
          <div
            className={clsx("h-full rounded-full", item.aligned ? "bg-accent-500" : "bg-slate-300")}
            style={{ width: `${item.contribution}%` }}
          />
        </div>
      </div>

      <p className="mt-4 truncate font-mono text-sm text-slate-400" title={item.citation}>
        {item.citation}
      </p>
    </div>
  );
}
