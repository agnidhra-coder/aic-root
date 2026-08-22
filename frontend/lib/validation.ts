const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateEmail(value: string): string | null {
  if (!value.trim()) return "Email is required.";
  if (!EMAIL_RE.test(value)) return "Enter a valid email address.";
  return null;
}

export function validateName(value: string): string | null {
  if (!value.trim()) return "Name is required.";
  if (value.trim().length < 2) return "Name must be at least 2 characters.";
  return null;
}

export interface PasswordRule {
  key: string;
  label: string;
  test: (value: string) => boolean;
}

export const passwordRules: PasswordRule[] = [
  { key: "length", label: "At least 8 characters", test: (v) => v.length >= 8 },
  { key: "lowercase", label: "One lowercase letter", test: (v) => /[a-z]/.test(v) },
  { key: "uppercase", label: "One uppercase letter", test: (v) => /[A-Z]/.test(v) },
  { key: "number", label: "One number", test: (v) => /\d/.test(v) },
  { key: "special", label: "One special character (e.g. !?#$%)", test: (v) => /[^a-zA-Z\d]/.test(v) },
];

export function validatePassword(value: string): string | null {
  if (!value) return "Password is required.";
  const failed = passwordRules.find((rule) => !rule.test(value));
  return failed ? `Password is missing: ${failed.label.toLowerCase()}.` : null;
}

export function validateLoginPassword(value: string): string | null {
  if (!value) return "Password is required.";
  return null;
}

export function validateConfirmPassword(password: string, confirm: string): string | null {
  if (!confirm) return "Confirm your password.";
  if (password !== confirm) return "Passwords don't match.";
  return null;
}
