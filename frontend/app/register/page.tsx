"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { clsx } from "clsx";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { AuthLayout } from "@/components/AuthLayout";
import { PasswordChecklist } from "@/components/PasswordChecklist";
import { PublicOnlyRoute } from "@/components/PublicOnlyRoute";
import { useAuth, ApiError } from "@/lib/auth-context";
import {
  validateConfirmPassword,
  validateEmail,
  validateName,
  validatePassword,
} from "@/lib/validation";

interface FormValues {
  name: string;
  email: string;
  password: string;
  confirmPassword: string;
}

type FieldErrors = Partial<Record<keyof FormValues, string>>;

function validateField(field: keyof FormValues, values: FormValues): string | null {
  switch (field) {
    case "name":
      return validateName(values.name);
    case "email":
      return validateEmail(values.email);
    case "password":
      return validatePassword(values.password);
    case "confirmPassword":
      return validateConfirmPassword(values.password, values.confirmPassword);
  }
}

const initialValues: FormValues = { name: "", email: "", password: "", confirmPassword: "" };

export default function RegisterPage() {
  const { register } = useAuth();
  const router = useRouter();
  const [values, setValues] = useState<FormValues>(initialValues);
  const [touched, setTouched] = useState<Partial<Record<keyof FormValues, boolean>>>({});
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  function setValue(field: keyof FormValues, value: string) {
    const next = { ...values, [field]: value };
    setValues(next);

    setFieldErrors((prev) => {
      const updated = { ...prev };
      if (touched[field]) {
        updated[field] = validateField(field, next) ?? undefined;
      }
      if (field === "password" && touched.confirmPassword) {
        updated.confirmPassword = validateConfirmPassword(next.password, next.confirmPassword) ?? undefined;
      }
      return updated;
    });
  }

  function handleBlur(field: keyof FormValues) {
    setTouched((prev) => ({ ...prev, [field]: true }));
    setFieldErrors((prev) => ({ ...prev, [field]: validateField(field, values) ?? undefined }));
  }

  function validateAll(): boolean {
    const errors: FieldErrors = {
      name: validateName(values.name) ?? undefined,
      email: validateEmail(values.email) ?? undefined,
      password: validatePassword(values.password) ?? undefined,
      confirmPassword: validateConfirmPassword(values.password, values.confirmPassword) ?? undefined,
    };
    setFieldErrors(errors);
    setTouched({ name: true, email: true, password: true, confirmPassword: true });
    return !errors.name && !errors.email && !errors.password && !errors.confirmPassword;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);

    if (!validateAll()) return;

    setIsSubmitting(true);
    try {
      await register(values.name, values.email, values.password);
      router.push("/dashboard");
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  const fieldClass = (hasError: boolean) =>
    clsx(
      "mt-1.5 w-full rounded-lg border px-3 py-2 text-sm text-slate-900 outline-none transition focus:ring-2",
      hasError
        ? "border-rose-300 focus:border-rose-400 focus:ring-rose-100"
        : "border-slate-200 focus:border-accent-500 focus:ring-accent-100"
    );

  return (
    <PublicOnlyRoute>
      <AuthLayout title="Create your account" subtitle="Get evidence-backed answers on every KPI movement.">
        <form onSubmit={handleSubmit} noValidate className="space-y-4">
          {formError && (
            <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600 ring-1 ring-inset ring-rose-100">
              {formError}
            </div>
          )}

          <div>
            <label htmlFor="name" className="text-sm font-medium text-slate-700">
              Name
            </label>
            <input
              id="name"
              name="name"
              type="text"
              autoComplete="name"
              value={values.name}
              onChange={(e) => setValue("name", e.target.value)}
              onBlur={() => handleBlur("name")}
              aria-invalid={!!fieldErrors.name}
              aria-describedby={fieldErrors.name ? "name-error" : undefined}
              className={fieldClass(!!fieldErrors.name)}
              placeholder="Jane Doe"
            />
            {fieldErrors.name && (
              <p id="name-error" className="mt-1.5 text-xs text-rose-600">
                {fieldErrors.name}
              </p>
            )}
          </div>

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
              className={fieldClass(!!fieldErrors.email)}
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
                autoComplete="new-password"
                value={values.password}
                onChange={(e) => setValue("password", e.target.value)}
                onBlur={() => handleBlur("password")}
                aria-invalid={!!fieldErrors.password}
                aria-describedby="password-requirements"
                className={clsx(fieldClass(!!fieldErrors.password), "pr-10")}
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
            <div id="password-requirements">
              <PasswordChecklist password={values.password} showInvalid={!!touched.password} />
            </div>
          </div>

          <div>
            <label htmlFor="confirmPassword" className="text-sm font-medium text-slate-700">
              Confirm password
            </label>
            <div className="relative mt-1.5">
              <input
                id="confirmPassword"
                name="confirmPassword"
                type={showConfirmPassword ? "text" : "password"}
                autoComplete="new-password"
                value={values.confirmPassword}
                onChange={(e) => setValue("confirmPassword", e.target.value)}
                onBlur={() => handleBlur("confirmPassword")}
                aria-invalid={!!fieldErrors.confirmPassword}
                aria-describedby={fieldErrors.confirmPassword ? "confirmPassword-error" : undefined}
                className={clsx(fieldClass(!!fieldErrors.confirmPassword), "pr-10")}
                placeholder="••••••••"
              />
              <button
                type="button"
                onClick={() => setShowConfirmPassword((v) => !v)}
                className="absolute inset-y-0 right-0 flex items-center px-3 text-slate-400 hover:text-slate-600"
                tabIndex={-1}
              >
                {showConfirmPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            {fieldErrors.confirmPassword && (
              <p id="confirmPassword-error" className="mt-1.5 text-xs text-rose-600">
                {fieldErrors.confirmPassword}
              </p>
            )}
          </div>

          <button
            type="submit"
            disabled={isSubmitting}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-accent-600 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isSubmitting && <Loader2 size={15} className="animate-spin" />}
            Create account
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-slate-500">
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-accent-600 hover:text-accent-700">
            Sign in
          </Link>
        </p>
      </AuthLayout>
    </PublicOnlyRoute>
  );
}
