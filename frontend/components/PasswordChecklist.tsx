import { clsx } from "clsx";
import { Check, Circle, X } from "lucide-react";
import { passwordRules } from "@/lib/validation";

export function PasswordChecklist({
  password,
  showInvalid = false,
}: {
  password: string;
  showInvalid?: boolean;
}) {
  return (
    <ul className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2">
      {passwordRules.map((rule) => {
        const met = rule.test(password);
        const invalid = !met && showInvalid;
        return (
          <li
            key={rule.key}
            className={clsx(
              "flex items-center gap-1.5 text-xs transition-colors",
              met && "text-emerald-600",
              invalid && "text-rose-600",
              !met && !invalid && "text-slate-400"
            )}
          >
            {met ? (
              <Check size={13} className="shrink-0" strokeWidth={3} />
            ) : invalid ? (
              <X size={13} className="shrink-0" strokeWidth={3} />
            ) : (
              <Circle size={13} className="shrink-0" />
            )}
            {rule.label}
          </li>
        );
      })}
    </ul>
  );
}
