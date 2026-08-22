import { Header } from "@/components/Header";
import { DashboardBoard } from "@/components/DashboardBoard";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { kpis } from "@/lib/data";

export default function DashboardPage() {
  return (
    <ProtectedRoute>
      <div className="flex min-h-screen flex-col bg-white">
        <Header />
        <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
          <div className="mb-8">
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900">KPI Overview</h1>
            <p className="mt-1 text-sm text-slate-500">
              Every movement below has already run through Detect → Decompose → Explain. Open a KPI to see the
              evidence and the recommended action.
            </p>
          </div>
          <DashboardBoard kpis={kpis} />
        </main>
        <footer className="border-t border-slate-100 py-6 text-center text-xs text-slate-400">
          Root — Team BIAI · Accenture Innovation Challenge 2026
        </footer>
      </div>
    </ProtectedRoute>
  );
}
