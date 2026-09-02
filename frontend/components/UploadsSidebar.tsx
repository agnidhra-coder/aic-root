"use client";

import { useState } from "react";
import { clsx } from "clsx";
import {
  CheckCircle2,
  CircleDot,
  FileSpreadsheet,
  Loader2,
  Plus,
  Trash2,
  XCircle,
} from "lucide-react";
import type { UploadRecord, UploadStatus } from "@/lib/api";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
}

function needsAttention(status: UploadStatus): boolean {
  return status === "awaiting_plan" || status === "awaiting_question";
}

function StatusIndicator({ status }: { status: UploadStatus }) {
  switch (status) {
    case "ready":
      return <CheckCircle2 size={14} className="shrink-0 text-emerald-500" />;
    case "failed":
      return <XCircle size={14} className="shrink-0 text-rose-500" />;
    case "awaiting_plan":
    case "awaiting_question":
      return <CircleDot size={14} className="shrink-0 text-amber-500" />;
    default:
      return <Loader2 size={14} className="shrink-0 animate-spin text-slate-400" />;
  }
}

function statusLabel(status: UploadStatus): string {
  switch (status) {
    case "pending":
      return "Queued";
    case "planning":
      return "Reading your columns…";
    case "awaiting_plan":
      return "Choose your KPIs";
    case "confirming":
      return "Configuring…";
    case "awaiting_question":
      return "Ask a question";
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
  onDelete,
}: {
  uploads: UploadRecord[];
  selectedId: string | null;
  onSelect: (upload: UploadRecord) => void;
  onUploadClick: () => void;
  onDelete: (upload: UploadRecord) => void;
}) {
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

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
        const isClickable = isReady || needsAttention(upload.status);
        const isSelected = upload.id === selectedId;
        const isConfirming = confirmingId === upload.id;
        return (
          <li key={upload.id}>
            <div
              role="button"
              tabIndex={isClickable ? 0 : -1}
              onClick={() => {
                if (isClickable) onSelect(upload);
              }}
              onKeyDown={(e) => {
                if (isClickable && (e.key === "Enter" || e.key === " ")) {
                  e.preventDefault();
                  onSelect(upload);
                }
              }}
              className={clsx(
                "group flex w-full items-start gap-2.5 rounded-lg border px-3 py-2.5 text-left transition",
                isSelected
                  ? "border-accent-500 bg-accent-50"
                  : "border-slate-200 bg-white hover:border-slate-300",
                isClickable ? "cursor-pointer" : "cursor-not-allowed opacity-60"
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

              {isConfirming ? (
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirmingId(null);
                      onDelete(upload);
                    }}
                    className="rounded-md bg-rose-500 px-2 py-1 text-xs font-semibold text-white transition hover:bg-rose-600"
                  >
                    Delete
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirmingId(null);
                    }}
                    className="rounded-md px-2 py-1 text-xs font-medium text-slate-500 transition hover:bg-slate-100"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmingId(upload.id);
                  }}
                  aria-label={`Delete ${upload.filename}`}
                  className="shrink-0 rounded-md p-1.5 text-slate-300 opacity-0 transition hover:bg-rose-50 hover:text-rose-600 group-hover:opacity-100"
                >
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
