import Link from "next/link";
import { Waypoints } from "lucide-react";

export function Header() {
  return (
    <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
        <Link href="/" className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-900 text-white">
            <Waypoints size={17} />
          </span>
          <span className="text-lg font-semibold tracking-tight text-slate-900">Root</span>
          <span className="hidden text-sm text-slate-400 sm:inline">the analyst that shows its work</span>
        </Link>
        <nav className="flex items-center gap-6 text-sm font-medium text-slate-500">
          <Link href="/" className="transition hover:text-slate-900">
            Dashboard
          </Link>
          <Link href="/contract" className="transition hover:text-slate-900">
            KPI Contracts
          </Link>
        </nav>
      </div>
    </header>
  );
}
