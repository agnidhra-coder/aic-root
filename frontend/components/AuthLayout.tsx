import Link from "next/link";
import { Waypoints, ShieldCheck, GitBranch, FileSearch } from "lucide-react";

const highlights = [
  {
    icon: ShieldCheck,
    title: "Evidence-cited, always",
    detail: "Every KPI movement is backed by structured, unstructured, and exogenous evidence you can trace.",
  },
  {
    icon: GitBranch,
    title: "Detect → Decompose → Explain → Act",
    detail: "One pipeline, run unmodified across retail and supply-chain domains.",
  },
  {
    icon: FileSearch,
    title: "Confidence you can defend",
    detail: "EXPLAINED, SUSPECTED, or UNEXPLAINED — never a black-box number.",
  },
];

export function AuthLayout({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col lg:flex-row">
      <div className="relative hidden flex-col justify-between overflow-hidden bg-slate-900 p-10 text-white lg:flex lg:w-[36%] lg:min-w-[420px] lg:max-w-md">
        <div className="absolute inset-0 bg-gradient-to-br from-accent-700 via-slate-900 to-slate-900" />
        <div className="relative z-10">
          <Link href="/" className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/10 backdrop-blur">
              <Waypoints size={17} />
            </span>
            <span className="text-lg font-semibold tracking-tight">Root</span>
          </Link>
        </div>

        <div className="relative z-10 space-y-8">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">The analyst that shows its work.</h2>
            <p className="mt-2 max-w-sm text-sm text-white/60">
              Root doesn&apos;t just flag that a KPI moved — it explains why, cites the evidence, and recommends
              the action.
            </p>
          </div>
          <div className="space-y-5">
            {highlights.map(({ icon: Icon, title: h, detail }) => (
              <div key={h} className="flex gap-3">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/10">
                  <Icon size={15} />
                </span>
                <div>
                  <p className="text-sm font-medium">{h}</p>
                  <p className="mt-0.5 text-xs text-white/50">{detail}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <p className="relative z-10 text-xs text-white/40">Root — Team BIAI · Accenture Innovation Challenge 2026</p>
      </div>

      <div className="flex flex-1 flex-col justify-center px-6 py-12 sm:px-12 lg:px-16">
        <div className="mx-auto w-full max-w-sm">
          <Link href="/" className="mb-8 flex items-center gap-2.5 lg:hidden">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-500 text-white">
              <Waypoints size={17} />
            </span>
            <span className="text-lg font-semibold tracking-tight text-slate-900">Root</span>
          </Link>

          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{title}</h1>
          <p className="mt-1 text-sm text-slate-500">{subtitle}</p>

          <div className="mt-8">{children}</div>
        </div>
      </div>
    </div>
  );
}
