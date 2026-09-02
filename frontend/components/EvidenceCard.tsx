import { clsx } from "clsx";
import { Database, FileText, CloudRain } from "lucide-react";
import type { EvidenceItem } from "@/lib/types";
import { MethodBadge } from "./MethodBadge";

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
        <MethodBadge exact={item.aligned} method={item.method} />
      </div>

      {item.detail && (
        <p className="mt-4 text-base leading-relaxed text-slate-600">{item.detail}</p>
      )}

      <div className="mt-4">
        <div className="flex items-center justify-between text-sm text-slate-400">
          <span>Contribution</span>
          <span className="font-medium text-slate-600">
            {item.contribution === null ? "not defined" : `${item.contribution}%`}
          </span>
        </div>
        <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-slate-100">
          {item.contribution !== null && (
            <div
              className={clsx("h-full rounded-full", item.aligned ? "bg-accent-500" : "bg-slate-300")}
              style={{ width: `${Math.min(100, Math.abs(item.contribution))}%` }}
            />
          )}
        </div>
      </div>

      <p className="mt-4 truncate font-mono text-sm text-slate-400" title={item.citation}>
        {item.citation}
      </p>
    </div>
  );
}
