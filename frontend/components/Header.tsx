"use client";

import Link from "next/link";
import { Waypoints, LogOut } from "lucide-react";
import { useAuth } from "@/lib/auth-context";

export function Header() {
  const { user, isLoading, logout } = useAuth();

  return (
    <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
        <Link href="/" className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-500 text-white">
            <Waypoints size={17} />
          </span>
          <span className="text-lg font-semibold tracking-tight text-slate-900">Root</span>
          <span className="hidden text-sm text-slate-400 sm:inline">the analyst that shows its work</span>
        </Link>
        <nav className="flex items-center gap-6 text-sm font-medium text-slate-500">
          <Link href="/dashboard" className="transition hover:text-accent-600">
            Dashboard
          </Link>
          <Link href="/contract" className="transition hover:text-accent-600">
            KPI Contracts
          </Link>

          {!isLoading && (
            <>
              {user ? (
                <div className="flex items-center gap-3 border-l border-slate-200 pl-6">
                  <span className="hidden text-slate-600 sm:inline">{user.name}</span>
                  <button
                    onClick={logout}
                    className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-slate-500 transition hover:bg-slate-50 hover:text-slate-900"
                  >
                    <LogOut size={14} />
                    Sign out
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-4 border-l border-slate-200 pl-6">
                  <Link href="/login" className="transition hover:text-accent-600">
                    Sign in
                  </Link>
                  <Link
                    href="/register"
                    className="rounded-lg bg-accent-500 px-3 py-1.5 text-white transition hover:bg-accent-600"
                  >
                    Sign up
                  </Link>
                </div>
              )}
            </>
          )}
        </nav>
      </div>
    </header>
  );
}
