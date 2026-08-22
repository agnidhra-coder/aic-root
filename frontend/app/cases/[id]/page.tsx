import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, CalendarCheck2 } from "lucide-react";
import { Header } from "@/components/Header";
import { PipelineStepper } from "@/components/PipelineStepper";
import { TierBadge } from "@/components/TierBadge";
import { EvidenceCard } from "@/components/EvidenceCard";
import { PersonaNarrative } from "@/components/PersonaNarrative";
import { ActionPlanCard } from "@/components/ActionPlanCard";
import { cases, kpis, personas, domainLabels } from "@/lib/data";

export function generateStaticParams() {
  return Object.keys(cases).map((id) => ({ id }));
}

export default async function CasePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const rootCase = cases[id];
  const kpi = kpis.find((k) => k.id === id);

  if (!rootCase || !kpi) notFound();

  const domainPersonas = personas.filter((p) =>
    rootCase.domain === "retail" ? p.id.startsWith("retail") : p.id.startsWith("sc")
  );

  return (
    <div className="flex min-h-screen flex-col bg-white">
      <Header />
      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
        <Link
          href="/dashboard"
          className="mb-6 inline-flex items-center gap-1.5 text-sm font-medium text-slate-500 transition hover:text-accent-600"
        >
          <ArrowLeft size={15} />
          Back to dashboard
        </Link>

        <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
              {domainLabels[rootCase.domain]} · {kpi.name}
            </p>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">{rootCase.title}</h1>
          </div>
          <TierBadge tier={rootCase.tier} className="mt-1" />
        </div>

        <div className="mb-10 rounded-2xl border border-slate-200 bg-slate-50/60 p-6">
          <PipelineStepper active="act" />
        </div>

        <section className="mb-10">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">1 · Detect</h2>
          <div className="rounded-xl border border-slate-200 bg-white p-5">
            <p className="text-sm font-medium text-slate-900">{rootCase.detect.headline}</p>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="rounded-lg bg-slate-50 px-3 py-2">
                <p className="text-xs text-slate-400">Statistical significance</p>
                <p className="text-sm text-slate-700">{rootCase.detect.statSignificance}</p>
              </div>
              <div className="rounded-lg bg-slate-50 px-3 py-2">
                <p className="text-xs text-slate-400">Business-impact floor</p>
                <p className="text-sm text-slate-700">{rootCase.detect.businessImpact}</p>
              </div>
            </div>
          </div>
        </section>

        <section className="mb-10">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">2 · Decompose</h2>
          <div className="flex flex-wrap items-center gap-2">
            {rootCase.decompose.map((step, i) => (
              <div key={step.dimension} className="flex items-center gap-2">
                <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
                  <p className="text-xs text-slate-400">{step.dimension}</p>
                  <p className="text-sm font-semibold text-slate-900">{step.narrowedTo}</p>
                  <p className="mt-0.5 text-xs text-slate-500">{step.note}</p>
                </div>
                {i < rootCase.decompose.length - 1 && <span className="text-slate-300">→</span>}
              </div>
            ))}
          </div>
        </section>

        <section className="mb-10">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">3 · Explain — Evidence</h2>
            <span className="text-sm font-medium text-slate-500">
              Combined contribution:{" "}
              <span className="font-semibold text-slate-900">{rootCase.contributionTotal}%</span>
            </span>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {rootCase.evidence.map((item) => (
              <EvidenceCard key={item.label} item={item} />
            ))}
          </div>
        </section>

        <section className="mb-10">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">4 · Act — Narrative by persona</h2>
          <PersonaNarrative personas={domainPersonas} narratives={rootCase.narratives} />
        </section>

        <section className="mb-10">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">Recommended action</h2>
          <ActionPlanCard action={rootCase.action} />
        </section>

        <section className="mb-4 flex items-center gap-2 rounded-xl border border-dashed border-slate-200 px-5 py-4 text-sm text-slate-500">
          <CalendarCheck2 size={16} className="text-slate-400" />
          6 · Feedback & Grade — check back on {rootCase.checkBackDate} to see if the predicted impact held.
        </section>
      </main>
      <footer className="border-t border-slate-100 py-6 text-center text-xs text-slate-400">
        Root — Team BIAI · Accenture Innovation Challenge 2026
      </footer>
    </div>
  );
}
