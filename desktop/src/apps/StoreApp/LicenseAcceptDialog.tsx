import { useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { useFocusTrap } from "@/hooks/use-focus-trap";

interface Props {
  appName: string;
  weightsLicense: string;
  text: string;
  submitting?: boolean;
  error?: string | null;
  /** Re-POSTs the install with accepted: true. */
  onAccept: () => void;
  onDecline: () => void;
}

/**
 * Single-accept license dialog shown before installing a service whose
 * backend pins non-commercial model weights (#169) -- e.g. musicgen's
 * MIT wrapper around Meta's CC-BY-NC 4.0 weights. Cloned from
 * PermissionConsent's modal shell (focus trap, portal, styling) but asks a
 * single "I agree" question about a weights license instead of a
 * multi-capability grant.
 */
export function LicenseAcceptDialog({
  appName,
  weightsLicense,
  text,
  submitting = false,
  error = null,
  onAccept,
  onDecline,
}: Props) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef, true);

  const handleAccept = useCallback(() => {
    if (!submitting) onAccept();
  }, [submitting, onAccept]);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={(e) => {
        if (submitting) return;
        if (e.target === e.currentTarget) onDecline();
      }}
      onKeyDown={(e) => {
        if (submitting) return;
        if (e.key === "Escape") onDecline();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={`${appName} weights license`}
        className="bg-shell-surface border border-white/10 shadow-2xl rounded-2xl w-[26rem] max-w-[90vw] flex flex-col"
      >
        <div className="px-5 py-4 border-b border-white/5">
          <h2 className="text-sm font-semibold text-shell-text">
            {appName} uses non-commercial model weights
          </h2>
        </div>
        <div className="px-5 py-4 space-y-2.5">
          <p className="text-[13px] text-shell-text-secondary leading-relaxed">{text}</p>
          <p className="text-[11px] text-shell-text-tertiary">
            Weights license: <span className="text-shell-text font-medium">{weightsLicense || "non-commercial (label unavailable)"}</span>
          </p>
        </div>
        {error && (
          <div role="alert" className="mx-5 mb-3 text-[12px] text-red-300 bg-red-500/10 border border-red-500/20 rounded px-2 py-1">
            {error}
          </div>
        )}
        <div className="flex justify-end gap-2 px-5 py-4 border-t border-white/5">
          <button
            type="button"
            onClick={onDecline}
            disabled={submitting}
            className="px-4 py-1.5 rounded-full text-[13px] font-semibold text-shell-text-secondary hover:text-shell-text hover:bg-white/[0.04] transition-colors disabled:opacity-60"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleAccept}
            disabled={submitting}
            className="px-4 py-1.5 rounded-full text-[13px] font-bold text-white transition-colors disabled:opacity-60"
            style={{ background: "var(--color-accent)" }}
          >
            {submitting ? "Installing…" : "Agree & Install"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default LicenseAcceptDialog;
