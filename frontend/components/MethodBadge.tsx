"use client";

import { useState } from "react";
import { clsx } from "clsx";

/**
 * "Certain" vs. "Estimated" for one number — whether it follows exactly from
 * a KPI's own formula, or comes from a statistical method instead. Same
 * hover/tap tooltip pattern as `TierBadge`, kept separate because it explains
 * a different axis (how sure is this specific number) from tier (how sure is
 * the overall explanation).
 */
export function MethodBadge({
  exact,
  method,
  className,
}: {
  exact: boolean;
  method?: string | null;
  className?: string;
}) {
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

  const label = exact ? "Certain" : "Estimated";
  const explanation = exact
    ? "This number comes directly from the KPI's own formula (e.g. Profit = Revenue − Costs) — not a statistical guess. It is as reliable as the data it was computed from."
    : `This number is a statistical estimate${method ? ` (method: ${method})` : ""}, not read straight from a formula. It carries real uncertainty a plain number does not show.`;

  return (
    <span className={clsx("relative inline-flex", className)}>
      <button
        type="button"
        onMouseEnter={show}
        onMouseLeave={hide}
        onClick={(e) => {
          e.stopPropagation();
          if (open) hide();
          else show();
        }}
        onBlur={hide}
        className={clsx(
          "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset",
          exact
            ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
            : "bg-amber-50 text-amber-700 ring-amber-200"
        )}
      >
        <span
          className={clsx(
            "h-1.5 w-1.5 rounded-full",
            exact ? "bg-emerald-500" : "bg-amber-500"
          )}
        />
        {label}
      </button>

      {mounted && (
        <span
          role="tooltip"
          onTransitionEnd={() => !open && setMounted(false)}
          className={clsx(
            "absolute bottom-full left-0 z-10 mb-2 w-64 rounded-lg border px-3 py-2 text-left text-xs font-normal leading-relaxed shadow-lg transition duration-150 ease-out",
            exact
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : "border-amber-200 bg-amber-50 text-amber-800",
            open ? "translate-y-0 opacity-100" : "translate-y-1 opacity-0"
          )}
        >
          {explanation}
        </span>
      )}
    </span>
  );
}
