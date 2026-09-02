"use client";

import { useMemo, useState } from "react";
import { clsx } from "clsx";
import { AlertCircle, ArrowRight, Check, Info, Loader2, Sparkles, X } from "lucide-react";
import type { KpiPlan, KpiProposal } from "@/lib/api";

function boundColumns(proposal: KpiProposal): string[] {
  return proposal.measures
    .map((m) => m.column)
    .filter((c): c is string => Boolean(c));
}

function friendlyCaveat(raw: string): string {
  const match = raw.match(/^'([^']+)' is barred as an independent driver: (.+)$/);
  if (!match) return raw;
  const [, column, why] = match;
  return `"${column}" won't be used to explain *why* this KPI moved, because it's ${lowerFirst(why)}. It's still included in the KPI's own calculation as normal.`;
}

function lowerFirst(text: string): string {
  return text.length > 0 ? text[0].toLowerCase() + text.slice(1) : text;
}

export function KpiPlanSelector({
  plan,
  isSubmitting,
  error,
  onConfirm,
}: {
  plan: KpiPlan;
  isSubmitting: boolean;
  error: string | null;
  onConfirm: (acceptedKpis: string[]) => void;
}) {
  const [accepted, setAccepted] = useState<Set<string>>(
    () => new Set(plan.proposed.filter((p) => p.recommended).map((p) => p.name))
  );

  const sorted = useMemo(
    () =>
      [...plan.proposed].sort((a, b) => {
        if (a.recommended !== b.recommended) return a.recommended ? -1 : 1;
        return a.name.localeCompare(b.name);
      }),
    [plan.proposed]
  );

  function toggle(name: string) {
    setAccepted((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  return (
    <div>
      <div className="mb-6">
        <h2 className="text-xl font-semibold tracking-tight text-slate-900">
          Which KPIs should we track?
        </h2>
        <p className="mt-1.5 text-base text-slate-500">
          We read the columns in your file and worked out which KPIs they can actually compute.
          Turn off anything you do not want measured.
        </p>
      </div>

      {plan.stagingWarnings && plan.stagingWarnings.length > 0 && (
        <div className="mb-6 flex items-start gap-3 rounded-xl border border-amber-100 bg-amber-50 px-5 py-4 text-sm text-amber-700">
          <AlertCircle size={18} className="mt-0.5 shrink-0" />
          <ul className="list-disc space-y-1 pl-4">
            {plan.stagingWarnings.map((warning, i) => (
              <li key={i}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      {plan.proposed.length === 0 ? (
        <div className="flex items-start gap-3 rounded-xl border border-amber-100 bg-amber-50 px-5 py-4 text-sm text-amber-700">
          <AlertCircle size={18} className="mt-0.5 shrink-0" />
          <p>
            None of the KPIs in our catalogue could be bound to the columns in this file. Try an
            extract with named measure columns, such as revenue, units, or cost.
          </p>
        </div>
      ) : (
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {sorted.map((proposal) => {
            const isAccepted = accepted.has(proposal.name);
            const columns = boundColumns(proposal);
            return (
              <li key={proposal.name}>
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => toggle(proposal.name)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      toggle(proposal.name);
                    }
                  }}
                  className={clsx(
                    "flex h-full w-full cursor-pointer items-start gap-3 rounded-xl border px-4 py-3.5 text-left transition",
                    isAccepted
                      ? "border-accent-500 bg-accent-50"
                      : "border-slate-200 bg-white hover:border-slate-300"
                  )}
                >
                  <span
                    className={clsx(
                      "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md border-2 transition",
                      isAccepted
                        ? "border-accent-500 bg-accent-500 text-white"
                        : "border-slate-300 bg-white text-transparent"
                    )}
                  >
                    {isAccepted ? <Check size={12} strokeWidth={3} /> : <X size={12} strokeWidth={3} />}
                  </span>

                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-base font-medium text-slate-900">{proposal.name}</span>
                      {proposal.recommended && (
                        <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
                          Recommended
                        </span>
                      )}
                      {!proposal.aggregation_safe && (
                        <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
                          Not safe to aggregate
                        </span>
                      )}
                    </span>

                    {proposal.doc_formula && (
                      <span className="mt-1 block font-mono text-xs text-slate-400">
                        {proposal.doc_formula}
                      </span>
                    )}

                    {columns.length > 0 && (
                      <span className="mt-1.5 flex flex-wrap gap-1.5">
                        {columns.map((column) => (
                          <span
                            key={column}
                            className="rounded-md bg-slate-100 px-2 py-0.5 text-xs text-slate-600"
                          >
                            {column}
                          </span>
                        ))}
                      </span>
                    )}

                    {proposal.caveats.length > 0 && (
                      <span className="mt-1.5 flex flex-wrap gap-1.5">
                        {proposal.caveats.map((caveat, i) => (
                          <CaveatNote key={i} message={friendlyCaveat(caveat)} />
                        ))}
                      </span>
                    )}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {plan.unavailable.length > 0 && (
        <details className="mt-6 rounded-xl border border-slate-200 bg-slate-50/60 px-4 py-3">
          <summary className="cursor-pointer text-sm font-medium text-slate-600">
            {plan.unavailable.length} KPI{plan.unavailable.length === 1 ? "" : "s"} could not be
            computed from this file
          </summary>
          <ul className="mt-3 space-y-2.5">
            {plan.unavailable.map((kpi) => (
              <li key={kpi.name} className="text-sm">
                <p className="font-medium text-slate-700">{kpi.name}</p>
                <p className="text-slate-500">
                  {kpi.reason ||
                    (kpi.missing_aliases.length > 0
                      ? `No column matched: ${kpi.missing_aliases.join(", ")}`
                      : "Not available for this file.")}
                </p>
              </li>
            ))}
          </ul>
        </details>
      )}

      {plan.unmatchedColumns && plan.unmatchedColumns.length > 0 && (
        <details className="mt-3 rounded-xl border border-slate-200 bg-slate-50/60 px-4 py-3">
          <summary className="cursor-pointer text-sm font-medium text-slate-600">
            {plan.unmatchedColumns.length} column
            {plan.unmatchedColumns.length === 1 ? "" : "s"} could not be placed against any KPI
          </summary>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {plan.unmatchedColumns.map((column) => (
              <span
                key={column}
                className="rounded-md bg-slate-100 px-2 py-0.5 text-xs text-slate-600"
              >
                {column}
              </span>
            ))}
          </div>
        </details>
      )}

      {error && (
        <div className="mt-4 whitespace-pre-line rounded-lg bg-rose-50 px-3 py-2.5 text-sm leading-relaxed text-rose-600 ring-1 ring-inset ring-rose-100">
          {error}
        </div>
      )}

      <div className="mt-6 flex items-center justify-between gap-4">
        <p className="text-sm text-slate-400">
          {accepted.size} of {plan.proposed.length} selected
        </p>
        <button
          type="button"
          onClick={() => onConfirm([...accepted])}
          disabled={accepted.size === 0 || isSubmitting}
          className="flex items-center justify-center gap-2 rounded-lg bg-accent-500 px-5 py-2.5 text-base font-semibold text-white transition hover:bg-accent-600 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isSubmitting ? <Loader2 size={17} className="animate-spin" /> : <Sparkles size={17} />}
          Confirm KPIs
          {!isSubmitting && <ArrowRight size={17} />}
        </button>
      </div>
    </div>
  );
}

function CaveatNote({ message }: { message: string }) {
  const [open, setOpen] = useState(false);

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((prev) => !prev);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        aria-label="Why this caveat applies"
        className="flex items-center gap-1 rounded-md bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700 transition hover:bg-amber-100"
      >
        <Info size={12} />
        Attribution note
      </button>

      {open && (
        <span
          role="tooltip"
          className="absolute bottom-full left-0 z-10 mb-2 w-64 rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-xs leading-relaxed text-slate-600 shadow-lg"
        >
          {message}
        </span>
      )}
    </span>
  );
}
