"use client";

import { useRef, useState } from "react";
import { clsx } from "clsx";
import {
  ArrowLeft,
  ArrowRight,
  FileSpreadsheet,
  FileText,
  Loader2,
  Sparkles,
  UploadCloud,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { ApiError, createUploadRequest, type UploadDomain, type UploadRecord } from "@/lib/api";
import { domainLabels } from "@/lib/data";
import { StepIndicator } from "./StepIndicator";

const CONTEXT_DOC_EXTENSIONS = [".pdf", ".docx", ".txt"];
const domains: UploadDomain[] = ["retail", "supply-chain"];

/**
 * The domain the upload is filed under.
 *
 * It is a label, not a contract: nothing validates the CSV against it and it is
 * never sent to the analysis engine — which derives what the file can compute
 * from the file's own columns. It only drives the dashboard's tab filter, so
 * this step just confirms (or overrides) the dashboard tab the user came from.
 */
const DEFAULT_DOMAIN: UploadDomain = "retail";

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
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [file, setFile] = useState<File | null>(null);
  const [contextDoc, setContextDoc] = useState<File | null>(null);
  const [domain, setDomain] = useState<UploadDomain>(initialDomain ?? DEFAULT_DOMAIN);
  const [isDragging, setIsDragging] = useState(false);
  const [isContextDragging, setIsContextDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const contextInputRef = useRef<HTMLInputElement>(null);

  function pickFile(selected: File | null) {
    setError(null);
    if (selected && !selected.name.toLowerCase().endsWith(".csv")) {
      setError("Only CSV files are accepted.");
      setFile(null);
      return;
    }
    setFile(selected);
  }

  function pickContextDoc(selected: File | null) {
    setError(null);
    if (selected && !CONTEXT_DOC_EXTENSIONS.some((ext) => selected.name.toLowerCase().endsWith(ext))) {
      setError("Only PDF, DOCX, or TXT files are accepted.");
      setContextDoc(null);
      return;
    }
    setContextDoc(selected);
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
    pickFile(e.dataTransfer.files[0] ?? null);
  }

  function handleContextDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsContextDragging(false);
    pickContextDoc(e.dataTransfer.files[0] ?? null);
  }

  function goToContextStep() {
    if (!file) return;
    setError(null);
    setStep(2);
  }

  function goBackToFileStep() {
    setError(null);
    setStep(1);
  }

  function goToDomainStep() {
    setError(null);
    setStep(3);
  }

  function goBackToContextStep() {
    setError(null);
    setStep(2);
  }

  async function handleAnalyze() {
    if (!file || !token) return;
    setIsUploading(true);
    setError(null);
    try {
      // The domain only files the upload for the dashboard's tab filter — it
      // does not gate the CSV and it never reaches the analysis engine.
      const upload = await createUploadRequest(token, {
        domain,
        file,
        contextDoc,
      });
      onUploaded(upload);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed. Please try again.");
      setIsUploading(false);
    }
  }

  return (
    <div>
      <StepIndicator currentStep={step} totalSteps={3} />

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
                onClick={goToContextStep}
                disabled={!file}
                className="flex items-center justify-center gap-2 rounded-lg bg-accent-500 px-5 py-2.5 text-base font-semibold text-white transition hover:bg-accent-600 disabled:cursor-not-allowed disabled:opacity-60"
              >
                Next
                <ArrowRight size={17} />
              </button>
            </div>
          </div>

          {/* Step 2: context document (optional) */}
          <div className="w-full shrink-0 pl-1">
            <button
              type="button"
              onClick={goBackToFileStep}
              className="mb-3 flex items-center gap-1 text-sm font-medium text-slate-400 transition hover:text-slate-600"
            >
              <ArrowLeft size={14} />
              Change file
            </button>

            <label className="text-base font-medium text-slate-700">
              Add supporting context <span className="font-normal text-slate-400">(optional)</span>
            </label>
            <p className="mt-1 text-sm text-slate-400">
              Upload a document with additional context, such as notes or a report, to help sharpen the analysis.
            </p>

            <div
              onDragOver={(e) => {
                e.preventDefault();
                setIsContextDragging(true);
              }}
              onDragLeave={() => setIsContextDragging(false)}
              onDrop={handleContextDrop}
              onClick={() => contextInputRef.current?.click()}
              className={clsx(
                "mt-3 flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 px-6 py-10 text-center transition",
                isContextDragging
                  ? "border-accent-400 bg-accent-50"
                  : "border-slate-200 bg-slate-50/60 hover:border-slate-300"
              )}
            >
              <input
                ref={contextInputRef}
                type="file"
                accept=".pdf,.docx,.txt"
                className="hidden"
                onChange={(e) => pickContextDoc(e.target.files?.[0] ?? null)}
              />
              {contextDoc ? (
                <>
                  <FileText size={28} className="text-accent-500" />
                  <p className="text-base font-medium text-slate-900">{contextDoc.name}</p>
                  <p className="text-sm text-slate-400">{formatFileSize(contextDoc.size)}</p>
                </>
              ) : (
                <>
                  <UploadCloud size={28} className="text-slate-300" />
                  <p className="text-base font-medium text-slate-600">Drag and drop a document, or click to browse</p>
                  <p className="text-sm text-slate-400">PDF, DOCX, or TXT, up to 20MB</p>
                </>
              )}
            </div>

            {contextDoc && (
              <button
                type="button"
                onClick={() => pickContextDoc(null)}
                className="mt-2 text-sm font-medium text-slate-400 transition hover:text-slate-600"
              >
                Remove document
              </button>
            )}

            {step === 2 && error && (
              <div className="mt-4 whitespace-pre-line rounded-lg bg-rose-50 px-3 py-2.5 text-sm leading-relaxed text-rose-600 ring-1 ring-inset ring-rose-100">
                {error}
              </div>
            )}

            <div className="mt-4 flex justify-end">
              <button
                type="button"
                onClick={goToDomainStep}
                className="flex items-center justify-center gap-2 rounded-lg bg-accent-500 px-5 py-2.5 text-base font-semibold text-white transition hover:bg-accent-600"
              >
                {contextDoc ? "Next" : "Skip"}
                <ArrowRight size={17} />
              </button>
            </div>
          </div>

          {/* Step 3: domain */}
          <div className="w-full shrink-0 pl-1">
            <button
              type="button"
              onClick={goBackToContextStep}
              className="mb-3 flex items-center gap-1 text-sm font-medium text-slate-400 transition hover:text-slate-600"
            >
              <ArrowLeft size={14} />
              Back
            </button>

            {file && (
              <div className="mb-4 flex items-center gap-2.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5">
                <FileSpreadsheet size={18} className="shrink-0 text-slate-400" />
                <p className="truncate text-base font-medium text-slate-700">{file.name}</p>
              </div>
            )}

            <label className="text-base font-medium text-slate-700">Which domain is this data for?</label>
            <p className="mt-1 text-sm text-slate-400">
              This only files the upload for the dashboard — it does not change what gets analyzed.
            </p>
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

            {step === 3 && error && (
              <div className="mt-4 whitespace-pre-line rounded-lg bg-rose-50 px-3 py-2.5 text-sm leading-relaxed text-rose-600 ring-1 ring-inset ring-rose-100">
                {error}
              </div>
            )}

            <div className="mt-4 flex justify-end">
              <button
                type="button"
                onClick={handleAnalyze}
                disabled={isUploading}
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
