"use client";

import { useState } from "react";
import { Loader2, Sparkles } from "lucide-react";

const BASE_QUESTION = "Analyze the KPIs I selected and tell me what needs attention.";

export function QuestionPrompt({
  isSubmitting,
  error,
  onSubmit,
}: {
  isSubmitting: boolean;
  error: string | null;
  onSubmit: (question: string) => void;
}) {
  const [question, setQuestion] = useState("");

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-6">
        <h2 className="text-xl font-semibold tracking-tight text-slate-900">
          Anything specific you want to know?
        </h2>
        <p className="mt-1.5 text-base text-slate-500">
          Optional. Leave this blank and we will look across every KPI you selected. Mentioning
          something the data does not contain — a campaign, an outage, a holiday — helps us place it
          against what we find.
        </p>
      </div>

      <div className="rounded-xl border border-slate-200 bg-slate-50/60 px-4 py-3">
        <p className="text-sm text-slate-500">We will ask:</p>
        <p className="mt-1 text-base text-slate-700">
          {BASE_QUESTION}
          {question.trim() && <span className="text-accent-600"> {question.trim()}</span>}
        </p>
      </div>

      <textarea
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        rows={4}
        maxLength={2000}
        placeholder="e.g. Focus on the West region in March. We ran an unlogged billboard campaign there from the 16th to the 31st."
        className="mt-4 w-full resize-y rounded-xl border border-slate-200 bg-white px-4 py-3 text-base text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-accent-400 focus:ring-2 focus:ring-accent-100"
      />

      {error && (
        <div className="mt-4 whitespace-pre-line rounded-lg bg-rose-50 px-3 py-2.5 text-sm leading-relaxed text-rose-600 ring-1 ring-inset ring-rose-100">
          {error}
        </div>
      )}

      <div className="mt-4 flex justify-end">
        <button
          type="button"
          onClick={() => onSubmit(question)}
          disabled={isSubmitting}
          className="flex items-center justify-center gap-2 rounded-lg bg-accent-500 px-5 py-2.5 text-base font-semibold text-white transition hover:bg-accent-600 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isSubmitting ? <Loader2 size={17} className="animate-spin" /> : <Sparkles size={17} />}
          Run analysis
        </button>
      </div>
    </div>
  );
}
