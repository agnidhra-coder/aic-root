import { clsx } from "clsx";
import { Search, GitBranch, FileSearch, MessageSquareText } from "lucide-react";

const steps = [
  { key: "detect", label: "Detect", icon: Search },
  { key: "decompose", label: "Decompose", icon: GitBranch },
  { key: "explain", label: "Explain", icon: FileSearch },
  { key: "act", label: "Act", icon: MessageSquareText },
];

export function PipelineStepper({ active }: { active: string }) {
  const activeIndex = steps.findIndex((s) => s.key === active);

  return (
    <div className="flex items-center">
      {steps.map((step, i) => {
        const Icon = step.icon;
        const done = i < activeIndex;
        const isActive = i === activeIndex;
        return (
          <div key={step.key} className="flex flex-1 items-center last:flex-none">
            <div className="flex flex-col items-center gap-2">
              <div
                className={clsx(
                  "flex h-9 w-9 items-center justify-center rounded-full border-2 transition",
                  isActive && "border-accent-500 bg-accent-500 text-white",
                  done && !isActive && "border-accent-500 bg-white text-accent-600",
                  !done && !isActive && "border-slate-200 bg-white text-slate-300"
                )}
              >
                <Icon size={16} />
              </div>
              <span
                className={clsx(
                  "text-xs font-medium",
                  isActive ? "text-accent-600" : done ? "text-slate-600" : "text-slate-300"
                )}
              >
                {step.label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <div className={clsx("mx-2 mb-5 h-px flex-1", i < activeIndex ? "bg-accent-500" : "bg-slate-200")} />
            )}
          </div>
        );
      })}
    </div>
  );
}
