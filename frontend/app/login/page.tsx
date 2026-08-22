"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { clsx } from "clsx";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { AuthLayout } from "@/components/AuthLayout";
import { PublicOnlyRoute } from "@/components/PublicOnlyRoute";
import { useAuth, ApiError } from "@/lib/auth-context";
import { validateEmail, validateLoginPassword } from "@/lib/validation";

interface FormValues {
  email: string;
  password: string;
}

type FieldErrors = Partial<Record<keyof FormValues, string>>;

function validateField(field: keyof FormValues, values: FormValues): string | null {
  switch (field) {
    case "email":
      return validateEmail(values.email);
    case "password":
      return validateLoginPassword(values.password);
  }
}

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [values, setValues] = useState<FormValues>({ email: "", password: "" });
  const [touched, setTouched] = useState<Partial<Record<keyof FormValues, boolean>>>({});
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [showPassword, setShowPassword] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  function setValue(field: keyof FormValues, value: string) {
    const next = { ...values, [field]: value };
    setValues(next);
    if (touched[field]) {
      setFieldErrors((prev) => ({ ...prev, [field]: validateField(field, next) ?? undefined }));
    }
  }

  function handleBlur(field: keyof FormValues) {
    setTouched((prev) => ({ ...prev, [field]: true }));
    setFieldErrors((prev) => ({ ...prev, [field]: validateField(field, values) ?? undefined }));
  }

  function validateAll(): boolean {
    const errors: FieldErrors = {
      email: validateEmail(values.email) ?? undefined,
      password: validateLoginPassword(values.password) ?? undefined,
    };
    setFieldErrors(errors);
    setTouched({ email: true, password: true });
    return !errors.email && !errors.password;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);

    if (!validateAll()) return;

    setIsSubmitting(true);
    try {
      await login(values.email, values.password);
      router.push("/dashboard");
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <PublicOnlyRoute>
      <AuthLayout title="Welcome back" subtitle="Sign in to see what moved and why.">
        <form onSubmit={handleSubmit} noValidate className="space-y-4">
          {formError && (
            <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600 ring-1 ring-inset ring-rose-100">
              {formError}
            </div>
          )}

          <div>
            <label htmlFor="email" className="text-sm font-medium text-slate-700">
              Email
            </label>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              value={values.email}
              onChange={(e) => setValue("email", e.target.value)}
              onBlur={() => handleBlur("email")}
              aria-invalid={!!fieldErrors.email}
              aria-describedby={fieldErrors.email ? "email-error" : undefined}
              className={clsx(
                "mt-1.5 w-full rounded-lg border px-3 py-2 text-sm text-slate-900 outline-none transition focus:ring-2",
                fieldErrors.email
                  ? "border-rose-300 focus:border-rose-400 focus:ring-rose-100"
                  : "border-slate-200 focus:border-accent-500 focus:ring-accent-100"
              )}
              placeholder="you@company.com"
            />
            {fieldErrors.email && (
              <p id="email-error" className="mt-1.5 text-xs text-rose-600">
                {fieldErrors.email}
              </p>
            )}
          </div>

          <div>
            <label htmlFor="password" className="text-sm font-medium text-slate-700">
              Password
            </label>
            <div className="relative mt-1.5">
              <input
                id="password"
                name="password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                value={values.password}
                onChange={(e) => setValue("password", e.target.value)}
                onBlur={() => handleBlur("password")}
                aria-invalid={!!fieldErrors.password}
                aria-describedby={fieldErrors.password ? "password-error" : undefined}
                className={clsx(
                  "w-full rounded-lg border px-3 py-2 pr-10 text-sm text-slate-900 outline-none transition focus:ring-2",
                  fieldErrors.password
                    ? "border-rose-300 focus:border-rose-400 focus:ring-rose-100"
                    : "border-slate-200 focus:border-accent-500 focus:ring-accent-100"
                )}
                placeholder="••••••••"
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                className="absolute inset-y-0 right-0 flex items-center px-3 text-slate-400 hover:text-slate-600"
                tabIndex={-1}
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            {fieldErrors.password && (
              <p id="password-error" className="mt-1.5 text-xs text-rose-600">
                {fieldErrors.password}
              </p>
            )}
          </div>

          <button
            type="submit"
            disabled={isSubmitting}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-accent-600 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isSubmitting && <Loader2 size={15} className="animate-spin" />}
            Sign in
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-slate-500">
          Don&apos;t have an account?{" "}
          <Link href="/register" className="font-medium text-accent-600 hover:text-accent-700">
            Sign up
          </Link>
        </p>
      </AuthLayout>
    </PublicOnlyRoute>
  );
}
