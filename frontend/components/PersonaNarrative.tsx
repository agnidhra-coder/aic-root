"use client";

import { useState } from "react";
import { clsx } from "clsx";
import type { Persona } from "@/lib/types";

export function PersonaNarrative({
  personas,
  narratives,
}: {
  personas: Persona[];
  narratives: Record<string, string>;
}) {
  const [activeId, setActiveId] = useState(personas[0]?.id);
  const active = personas.find((p) => p.id === activeId) ?? personas[0];

  return (
    <div className="rounded-xl border border-slate-200 bg-white">
      <div className="flex border-b border-slate-100 px-2 pt-2">
        {personas.map((p) => (
          <button
            key={p.id}
            onClick={() => setActiveId(p.id)}
            className={clsx(
              "rounded-t-lg px-4 py-2.5 text-sm font-medium transition",
              p.id === activeId
                ? "border-b-2 border-accent-500 text-accent-600"
                : "text-slate-400 hover:text-slate-600"
            )}
          >
            {p.name}
          </button>
        ))}
      </div>
      <div className="p-5">
        <p className="text-xs text-slate-400">{active?.scope}</p>
        <p className="mt-2 text-[15px] leading-relaxed text-slate-700">
          {active ? narratives[active.id] : ""}
        </p>
      </div>
    </div>
  );
}
