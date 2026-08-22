import Link from "next/link";
import { ArrowRight, ShieldCheck, GitBranch, FileSearch, Sparkles } from "lucide-react";
import { Header } from "@/components/Header";
import { PipelineStepper } from "@/components/PipelineStepper";
import { PublicOnlyRoute } from "@/components/PublicOnlyRoute";

const features = [
  {
    icon: ShieldCheck,
    title: "Evidence-cited, always",
    detail:
      "Every KPI movement is backed by structured, unstructured, and exogenous evidence you can trace back to its source.",
  },
  {
    icon: GitBranch,
    title: "One pipeline, every domain",
    detail:
      "Detect → Decompose → Explain → Act runs unmodified across retail and supply-chain, driven by contract, not code.",
  },
  {
    icon: FileSearch,
    title: "Confidence you can defend",
    detail: "Every answer is tiered EXPLAINED, SUSPECTED, or UNEXPLAINED — never a black-box number with no receipt.",
  },
];

export default function LandingPage() {
  return (
    <PublicOnlyRoute>
      <div className="flex min-h-screen flex-col bg-white">
        <Header />

        <main className="flex-1">
          <section className="relative overflow-hidden border-b border-slate-100">
            <div className="absolute inset-0 -z-10 bg-gradient-to-br from-accent-50 via-white to-white" />
            <div className="grid grid-cols-1 items-center gap-12 px-6 py-20 sm:px-12 lg:grid-cols-2 lg:px-20 lg:py-28 xl:px-28">
              <div>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-accent-50 px-3 py-1 text-xs font-medium text-accent-700 ring-1 ring-inset ring-accent-100">
                  <Sparkles size={13} />
                  Team BIAI · Accenture Innovation Challenge 2026
                </span>

                <h1 className="mt-6 text-4xl font-semibold tracking-tight text-slate-900 sm:text-5xl xl:text-6xl">
                  The analyst that shows its work.
                </h1>
                <p className="mt-5 max-w-xl text-base text-slate-500 sm:text-lg">
                  Root doesn&apos;t just flag that a KPI moved. It decomposes the movement, cites the evidence, and
                  recommends the action — every time.
                </p>

                <div className="mt-8 flex flex-col gap-3 sm:flex-row">
                  <Link
                    href="/register"
                    className="flex items-center justify-center gap-2 rounded-lg bg-accent-500 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-accent-600"
                  >
                    Get started free
                    <ArrowRight size={15} />
                  </Link>
                  <Link
                    href="/login"
                    className="flex items-center justify-center rounded-lg border border-slate-200 px-5 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
                  >
                    Sign in
                  </Link>
                </div>
              </div>

              <div className="rounded-2xl border border-slate-200 bg-white p-8 shadow-sm lg:p-10">
                <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Every case, every time</p>
                <div className="mt-6">
                  <PipelineStepper active="act" />
                </div>
                <div className="mt-8 space-y-3 border-t border-slate-100 pt-6">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-slate-500">Revenue — West region</span>
                    <span className="font-medium text-emerald-600">EXPLAINED</span>
                  </div>
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-slate-500">OTIF — DC-14</span>
                    <span className="font-medium text-amber-600">SUSPECTED</span>
                  </div>
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-slate-500">Return rate — Apparel</span>
                    <span className="font-medium text-emerald-600">EXPLAINED</span>
                  </div>
                </div>
              </div>
            </div>
          </section>

          <section className="border-b border-slate-100 bg-slate-50/50">
            <div className="px-6 py-16 sm:px-12 lg:px-20 xl:px-28">
              <div className="mb-10">
                <h2 className="text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">
                  Built to be trusted, not just read
                </h2>
                <p className="mt-2 max-w-xl text-sm text-slate-500 sm:text-base">
                  Everything an analyst would ask for, before they ask.
                </p>
              </div>
              <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
                {features.map(({ icon: Icon, title, detail }) => (
                  <div key={title} className="rounded-2xl border border-slate-200 bg-white p-6">
                    <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-50 text-accent-600">
                      <Icon size={17} />
                    </span>
                    <h3 className="mt-4 text-sm font-semibold text-slate-900">{title}</h3>
                    <p className="mt-1.5 text-sm text-slate-500">{detail}</p>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <section className="px-6 py-16 sm:px-12 lg:px-20 xl:px-28">
            <div className="flex flex-col items-center gap-4 rounded-2xl bg-slate-900 px-8 py-14 text-center sm:px-16">
              <h2 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">
                Ready to see what moved, and why?
              </h2>
              <p className="max-w-md text-sm text-white/60">
                Create an account and open your first KPI drill-down in under a minute.
              </p>
              <Link
                href="/register"
                className="mt-2 flex items-center gap-2 rounded-lg bg-accent-500 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-accent-600"
              >
                Sign up free
                <ArrowRight size={15} />
              </Link>
            </div>
          </section>
        </main>

        <footer className="border-t border-slate-100 py-6 text-center text-xs text-slate-400">
          Root — Team BIAI · Accenture Innovation Challenge 2026
        </footer>
      </div>
    </PublicOnlyRoute>
  );
}
