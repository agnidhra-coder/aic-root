import { clsx } from "clsx";
import { Search, GitBranch, FileSearch, MessageSquareText } from "lucide-react";

const steps = [
  { key: "detect", label: "Detect", icon: Search },
  { key: "decompose", label: "Decompose", icon: GitBranch },
  { key: "explain", label: "Explain", icon: FileSearch },
  { key: "act", label: "Act", icon: MessageSquareText },
];

export function PipelineStepper({ active, size = "md" }: { active: string; size?: "md" | "lg" }) {
  const activeIndex = steps.findIndex((s) => s.key === active);
  const isLarge = size === "lg";

  return (
    <div className="flex items-center">
      {steps.map((step, i) => {
        const Icon = step.icon;
        const done = i < activeIndex;
        const isActive = i === activeIndex;
        return (
          <div key={step.key} className="flex flex-1 items-center last:flex-none">
            <div className={clsx("flex flex-col items-center", isLarge ? "gap-3" : "gap-2")}>
              <div
                className={clsx(
                  "flex items-center justify-center rounded-full border-2 transition-colors duration-500 ease-out",
                  isLarge ? "h-14 w-14" : "h-9 w-9",
                  isActive && "border-accent-500 bg-accent-500 text-white",
                  done && !isActive && "border-accent-500 bg-white text-accent-600",
                  !done && !isActive && "border-slate-200 bg-white text-slate-300"
                )}
              >
                <Icon size={isLarge ? 24 : 16} />
              </div>
              <span
                className={clsx(
                  "transition-colors duration-500 ease-out",
                  isLarge ? "text-base font-semibold" : "text-xs font-medium",
                  isActive ? "text-accent-600" : done ? "text-slate-600" : "text-slate-300"
                )}
              >
                {step.label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <div
                className={clsx(
                  "mx-2 flex-1 overflow-hidden rounded-full bg-slate-200",
                  isLarge ? "mb-7 h-1" : "mb-5 h-px"
                )}
              >
                <div
                  className={clsx(
                    "h-full rounded-full bg-accent-500 transition-transform duration-500 ease-out",
                    i < activeIndex ? "translate-x-0" : "-translate-x-full"
                  )}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
