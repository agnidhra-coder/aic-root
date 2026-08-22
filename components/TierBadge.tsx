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

export function TierBadge({ tier, className }: { tier: ConfidenceTier; className?: string }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset",
        styles[tier],
        className
      )}
    >
      <span className={clsx("h-1.5 w-1.5 rounded-full", dot[tier])} />
      {tier}
    </span>
  );
}
