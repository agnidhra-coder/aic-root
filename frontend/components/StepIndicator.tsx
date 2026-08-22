"use client";

import { clsx } from "clsx";
import { Check } from "lucide-react";

export function StepIndicator({ currentStep, totalSteps }: { currentStep: number; totalSteps: number }) {
  return (
    <div className="mx-auto mb-6 flex w-40 items-center">
      {Array.from({ length: totalSteps }, (_, i) => i + 1).map((step, i) => {
        const isDone = step < currentStep;
        const isActive = step === currentStep;
        return (
          <div key={step} className="flex flex-1 items-center last:flex-none">
            <div
              className={clsx(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2 text-xs font-semibold transition-colors duration-300",
                isActive && "border-accent-500 bg-accent-500 text-white",
                isDone && !isActive && "border-accent-500 bg-accent-500 text-white",
                !isDone && !isActive && "border-slate-200 bg-white text-slate-400"
              )}
            >
              {isDone ? <Check size={13} strokeWidth={3} /> : step}
            </div>
            {i < totalSteps - 1 && (
              <div className="mx-2 h-0.5 flex-1 overflow-hidden rounded-full bg-slate-200">
                <div
                  className={clsx(
                    "h-full rounded-full bg-accent-500 transition-transform duration-500 ease-out",
                    step < currentStep ? "translate-x-0" : "-translate-x-full"
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
