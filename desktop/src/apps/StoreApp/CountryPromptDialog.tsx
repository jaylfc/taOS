import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useFocusTrap } from "@/hooks/use-focus-trap";
import { CountrySelect } from "@/components/CountrySelect";

interface Props {
  appName: string;
  detail: string;
  submitting?: boolean;
  error?: string | null;
  /** Re-POSTs the install with the chosen country; the server saves it to the person's profile. */
  onSave: (code: string) => void;
  onDecline: () => void;
}

/**
 * Asked when a territory-limited licence needs the person's country and it is
 * not set yet. The same value can be set any time in Settings, Account.
 */
export function CountryPromptDialog({ appName, detail, submitting = false, error = null, onSave, onDecline }: Props) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef, true);
  const [code, setCode] = useState("");

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={(e) => { if (!submitting && e.target === e.currentTarget) onDecline(); }}
      onKeyDown={(e) => { if (!submitting && e.key === "Escape") onDecline(); }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={`${appName} country`}
        className="bg-shell-surface border border-white/10 shadow-2xl rounded-2xl w-[26rem] max-w-[90vw] flex flex-col"
      >
        <div className="px-5 py-4 border-b border-white/5">
          <h2 className="text-sm font-semibold text-shell-text">Which country are you in?</h2>
        </div>
        <div className="px-5 py-4 space-y-3">
          <p className="text-[13px] text-shell-text-secondary leading-relaxed">{detail}</p>
          <CountrySelect value={code} onChange={setCode} disabled={submitting} aria-label="Your country" />
          <p className="text-[11px] text-shell-text-tertiary">
            Saved to your account. You can change it in Settings, Account.
          </p>
        </div>
        {error && (
          <div role="alert" className="mx-5 mb-3 text-[12px] text-red-300 bg-red-500/10 border border-red-500/20 rounded px-2 py-1">
            {error}
          </div>
        )}
        <div className="flex justify-end gap-2 px-5 py-4 border-t border-white/5">
          <button type="button" onClick={onDecline} disabled={submitting}
            className="px-4 py-1.5 min-h-[44px] rounded-full text-[13px] font-semibold text-shell-text-secondary hover:text-shell-text hover:bg-white/[0.04] transition-colors disabled:opacity-60">
            Cancel
          </button>
          <button type="button" onClick={() => { if (code && !submitting) onSave(code); }} disabled={!code || submitting}
            className="px-4 py-1.5 min-h-[44px] rounded-full text-[13px] font-bold text-white transition-colors disabled:opacity-60"
            style={{ background: "var(--color-accent)" }}>
            {submitting ? "Saving..." : "Save and continue"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default CountryPromptDialog;
