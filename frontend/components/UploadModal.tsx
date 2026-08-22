"use client";

import { X } from "lucide-react";
import { UploadWizard } from "./UploadWizard";
import type { UploadDomain, UploadRecord } from "@/lib/api";

export function UploadModal({
  onClose,
  onUploaded,
  initialDomain,
}: {
  onClose: () => void;
  onUploaded: (upload: UploadRecord) => void;
  initialDomain?: UploadDomain;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl rounded-2xl bg-white p-8 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xl font-semibold tracking-tight text-slate-900">Upload KPI Data</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-50 hover:text-slate-600"
          >
            <X size={18} />
          </button>
        </div>

        <UploadWizard onUploaded={onUploaded} initialDomain={initialDomain} />
      </div>
    </div>
  );
}
