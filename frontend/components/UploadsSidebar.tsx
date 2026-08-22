"use client";

import { clsx } from "clsx";
import { CheckCircle2, FileSpreadsheet, Loader2, Plus, XCircle } from "lucide-react";
import type { UploadRecord, UploadStatus } from "@/lib/api";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
}

function StatusIndicator({ status }: { status: UploadStatus }) {
  switch (status) {
    case "ready":
      return <CheckCircle2 size={14} className="shrink-0 text-emerald-500" />;
    case "failed":
      return <XCircle size={14} className="shrink-0 text-rose-500" />;
    default:
      return <Loader2 size={14} className="shrink-0 animate-spin text-slate-400" />;
  }
}

function statusLabel(status: UploadStatus): string {
  switch (status) {
    case "pending":
      return "Queued";
    case "analyzing":
      return "Analyzing…";
    case "ready":
      return "Ready";
    case "failed":
      return "Failed";
  }
}

export function UploadsSidebar({
  uploads,
  selectedId,
  onSelect,
  onUploadClick,
}: {
  uploads: UploadRecord[];
  selectedId: string | null;
  onSelect: (upload: UploadRecord) => void;
  onUploadClick: () => void;
}) {
  if (uploads.length === 0) {
    return (
      <div className="flex flex-col items-center gap-3 rounded-xl bg-slate-50 px-4 py-8 text-center">
        <p className="text-sm text-slate-400">No files uploaded for this domain yet</p>
        <button
          type="button"
          onClick={onUploadClick}
          className="flex items-center gap-1.5 rounded-lg bg-accent-500 px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-accent-600"
        >
          <Plus size={14} />
          Upload
        </button>
      </div>
    );
  }

  return (
    <ul className="space-y-1.5">
      {uploads.map((upload) => {
        const isReady = upload.status === "ready";
        const isSelected = upload.id === selectedId;
        return (
          <li key={upload.id}>
            <button
              type="button"
              disabled={!isReady}
              onClick={() => onSelect(upload)}
              className={clsx(
                "flex w-full items-start gap-2.5 rounded-lg border px-3 py-2.5 text-left transition",
                isSelected
                  ? "border-accent-500 bg-accent-50"
                  : "border-slate-200 bg-white hover:border-slate-300",
                !isReady && "cursor-not-allowed opacity-60"
              )}
            >
              <FileSpreadsheet size={16} className="mt-0.5 shrink-0 text-slate-400" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-slate-900">{upload.filename}</p>
                <div className="mt-0.5 flex items-center gap-1.5 text-xs text-slate-400">
                  <StatusIndicator status={upload.status} />
                  <span>{statusLabel(upload.status)}</span>
                  <span>·</span>
                  <span>{formatDate(upload.created_at)}</span>
                </div>
              </div>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
