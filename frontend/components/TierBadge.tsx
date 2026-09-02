"use client";

import { useState } from "react";
import type { ConfidenceTier } from "@/lib/types";
import { clsx } from "clsx";

const styles: Record<ConfidenceTier, string> = {
  EXPLAINED: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  SUSPECTED: "bg-amber-50 text-amber-700 ring-amber-200",
  UNEXPLAINED: "bg-slate-100 text-slate-600 ring-slate-200",
};

const dot: Record<ConfidenceTier, string> = {
  EXPLAINED: "bg-emerald-500",
  SUSPECTED: "bg-amber-500",
  UNEXPLAINED: "bg-slate-400",
};

const tooltipStyles: Record<ConfidenceTier, string> = {
  EXPLAINED: "border-emerald-200 bg-emerald-50 text-emerald-800",
  SUSPECTED: "border-amber-200 bg-amber-50 text-amber-800",
  UNEXPLAINED: "border-slate-200 bg-slate-50 text-slate-600",
};

const explanation: Record<ConfidenceTier, string> = {
  EXPLAINED:
    "The engine found drivers whose combined contribution accounts for most of this movement. High confidence in the cause.",
  SUSPECTED:
    "The engine found drivers that contribute to this movement, but not enough to call it fully explained. Partial confidence in the cause.",
  UNEXPLAINED:
    "The engine detected this movement but found no drivers meeting the confidence bar. This is an abstention, not a failure — it found the move but not enough evidence to say what caused it.",
};

export function TierBadge({ tier, className }: { tier: ConfidenceTier; className?: string }) {
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);

  function show() {
    setMounted(true);
    requestAnimationFrame(() => {
      requestAnimationFrame(() => setOpen(true));
    });
  }

  function hide() {
    setOpen(false);
  }

  return (
    <span className="relative inline-flex">
      <button
        type="button"
        onMouseEnter={show}
        onMouseLeave={hide}
        onClick={() => (open ? hide() : show())}
        onBlur={hide}
        className={clsx(
          "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset",
          styles[tier],
          className
        )}
      >
        <span className={clsx("h-1.5 w-1.5 rounded-full", dot[tier])} />
        {tier}
      </button>

      {mounted && (
        <span
          role="tooltip"
          onTransitionEnd={() => !open && setMounted(false)}
          className={clsx(
            "absolute bottom-full left-1/2 z-10 mb-2 w-64 -translate-x-1/2 rounded-lg border px-3 py-2 text-left text-xs font-normal leading-relaxed shadow-lg transition duration-150 ease-out",
            tooltipStyles[tier],
            open ? "translate-y-0 opacity-100" : "translate-y-1 opacity-0"
          )}
        >
          {explanation[tier]}
        </span>
      )}
    </span>
  );
}
