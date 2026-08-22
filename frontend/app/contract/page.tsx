import { Header } from "@/components/Header";
import { contracts } from "@/lib/contracts";
import { domainLabels } from "@/lib/data";
import type { Domain } from "@/lib/types";

const sections: { key: keyof (typeof contracts)["retail"]; label: string }[] = [
  { key: "kpis", label: "KPIs" },
  { key: "dimensions", label: "Decomposition dimensions" },
  { key: "drivers", label: "Candidate drivers" },
  { key: "structured", label: "Structured evidence" },
  { key: "unstructured", label: "Unstructured evidence" },
  { key: "exogenous", label: "Exogenous evidence" },
  { key: "personas", label: "Personas" },
];

function ContractCard({ domain }: { domain: Domain }) {
  const contract = contracts[domain];
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-6">
      <h2 className="text-lg font-semibold text-slate-900">{domainLabels[domain]}</h2>
      <p className="mt-1 text-xs text-slate-400">KPI semantic contract · v1</p>
      <div className="mt-5 space-y-4">
        {sections.map(({ key, label }) => (
          <div key={key}>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {(contract[key] as string[]).map((v) => (
                <span
                  key={v}
                  className="rounded-full bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-200"
                >
                  {v}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ContractPage() {
  return (
    <div className="flex min-h-screen flex-col bg-white">
      <Header />
      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">KPI Semantic Contracts</h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Everything domain-specific — KPI definitions, evidence sources, driver candidates, personas — lives in
            a versioned contract, not in the engine. The same Detect → Decompose → Explain → Act pipeline runs
            unmodified against both.
          </p>
        </div>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <ContractCard domain="retail" />
          <ContractCard domain="supply-chain" />
        </div>
      </main>
      <footer className="border-t border-slate-100 py-6 text-center text-xs text-slate-400">
        Root — Team BIAI · Accenture Innovation Challenge 2026
      </footer>
    </div>
  );
}
