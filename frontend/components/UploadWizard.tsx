"use client";

import { useRef, useState } from "react";
import { clsx } from "clsx";
import { ArrowLeft, ArrowRight, FileSpreadsheet, Loader2, Sparkles, UploadCloud } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { ApiError, createUploadRequest, type UploadDomain, type UploadRecord } from "@/lib/api";
import { domainLabels } from "@/lib/data";
import { StepIndicator } from "./StepIndicator";

const domains: UploadDomain[] = ["retail", "supply-chain"];

function formatFileSize(bytes: number): string {
  const kb = bytes / 1024;
  if (kb >= 1000) return `${(kb / 1024).toFixed(1)} MB`;
  return `${kb.toFixed(1)} KB`;
}

export function UploadWizard({
  onUploaded,
  initialDomain = null,
}: {
  onUploaded: (upload: UploadRecord) => void;
  initialDomain?: UploadDomain | null;
}) {
  const { token } = useAuth();
  const [step, setStep] = useState<1 | 2>(1);
  const [file, setFile] = useState<File | null>(null);
  const [domain, setDomain] = useState<UploadDomain | null>(initialDomain);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function pickFile(selected: File | null) {
    setError(null);
    if (selected && !selected.name.toLowerCase().endsWith(".csv")) {
      setError("Only CSV files are accepted.");
      setFile(null);
      return;
    }
    setFile(selected);
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
    pickFile(e.dataTransfer.files[0] ?? null);
  }

  function goToDomainStep() {
    if (!file) return;
    setError(null);
    setStep(2);
  }

  function goBackToFileStep() {
    setError(null);
    setStep(1);
  }

  async function handleAnalyze() {
    if (!file || !domain || !token) return;
    setIsUploading(true);
    setError(null);
    try {
      const upload = await createUploadRequest(token, { domain, file });
      onUploaded(upload);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed. Please try again.");
      setIsUploading(false);
    }
  }

  return (
    <div>
      <StepIndicator currentStep={step} totalSteps={2} />

      <div className="overflow-hidden">
        <div
          className="flex transition-transform duration-300 ease-out"
          style={{ transform: `translateX(-${(step - 1) * 100}%)` }}
        >
          {/* Step 1: file */}
          <div className="w-full shrink-0">
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={clsx(
                "flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 px-6 py-14 text-center transition",
                isDragging ? "border-accent-400 bg-accent-50" : "border-slate-200 bg-slate-50/60 hover:border-slate-300"
              )}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,text/csv"
                className="hidden"
                onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
              />
              {file ? (
                <>
                  <FileSpreadsheet size={32} className="text-accent-500" />
                  <p className="text-base font-medium text-slate-900">{file.name}</p>
                  <p className="text-sm text-slate-400">{formatFileSize(file.size)}</p>
                </>
              ) : (
                <>
                  <UploadCloud size={32} className="text-slate-300" />
                  <p className="text-base font-medium text-slate-600">Drag and drop a CSV, or click to browse</p>
                  <p className="text-sm text-slate-400">CSV files only, up to 20MB</p>
                </>
              )}
            </div>

            {step === 1 && error && (
              <div className="mt-4 whitespace-pre-line rounded-lg bg-rose-50 px-3 py-2.5 text-sm leading-relaxed text-rose-600 ring-1 ring-inset ring-rose-100">
                {error}
              </div>
            )}

            <div className="mt-4 flex justify-end">
              <button
                type="button"
                onClick={goToDomainStep}
                disabled={!file}
                className="flex items-center justify-center gap-2 rounded-lg bg-accent-500 px-5 py-2.5 text-base font-semibold text-white transition hover:bg-accent-600 disabled:cursor-not-allowed disabled:opacity-60"
              >
                Next
                <ArrowRight size={17} />
              </button>
            </div>
          </div>

          {/* Step 2: domain */}
          <div className="w-full shrink-0 pl-1">
            <button
              type="button"
              onClick={goBackToFileStep}
              className="mb-3 flex items-center gap-1 text-sm font-medium text-slate-400 transition hover:text-slate-600"
            >
              <ArrowLeft size={14} />
              Change file
            </button>

            {file && (
              <div className="mb-4 flex items-center gap-2.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5">
                <FileSpreadsheet size={18} className="shrink-0 text-slate-400" />
                <p className="truncate text-base font-medium text-slate-700">{file.name}</p>
              </div>
            )}

            <label className="text-base font-medium text-slate-700">Which domain is this data for?</label>
            <div className="mt-2 flex gap-2">
              {domains.map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => setDomain(d)}
                  className={clsx(
                    "rounded-lg border px-3.5 py-2 text-base font-medium transition",
                    domain === d
                      ? "border-accent-500 bg-accent-50 text-accent-700"
                      : "border-slate-200 text-slate-600 hover:border-slate-300"
                  )}
                >
                  {domainLabels[d]}
                </button>
              ))}
            </div>

            {step === 2 && error && (
              <div className="mt-4 whitespace-pre-line rounded-lg bg-rose-50 px-3 py-2.5 text-sm leading-relaxed text-rose-600 ring-1 ring-inset ring-rose-100">
                {error}
              </div>
            )}

            <div className="mt-4 flex justify-end">
              <button
                type="button"
                onClick={handleAnalyze}
                disabled={!domain || isUploading}
                className="flex items-center justify-center gap-2 rounded-lg bg-accent-500 px-5 py-2.5 text-base font-semibold text-white transition hover:bg-accent-600 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isUploading ? <Loader2 size={17} className="animate-spin" /> : <Sparkles size={17} />}
                Analyze
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
