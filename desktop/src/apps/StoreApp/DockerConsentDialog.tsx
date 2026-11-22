import { useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { Package } from "lucide-react";
import { useFocusTrap } from "@/hooks/use-focus-trap";

interface Props {
  appId: string;
  command: string;
  message: string;
  submitting?: boolean;
  error?: string | null;
  /** Re-POSTs the install with docker_consent: true. */
  onAccept: () => void;
  onDecline: () => void;
}

/**
 * Single-accept Docker consent dialog shown before installing a service that
 * requires Docker. Explains what Docker installation entails and provides
 * the exact command the owner must run.
 */
export function DockerConsentDialog({
  appId,
  command,
  message,
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
        aria-label={`${appId} requires Docker`}
        className="bg-shell-surface border border-white/10 shadow-2xl rounded-2xl w-[28rem] max-w-[90vw] flex flex-col"
      >
        <div className="px-5 py-4 border-b border-white/5">
          <h2 className="text-sm font-semibold text-shell-text flex items-center gap-2">
            <Package className="w-4 h-4 text-blue-400" />
            Docker Required
          </h2>
        </div>
        <div className="px-5 py-4 space-y-3">
          <p className="text-[13px] text-shell-text-secondary leading-relaxed">{message}</p>
          {command && (
            <div className="bg-shell-bg-deep border border-white/5 rounded-lg p-3">
              <p className="text-[11px] font-medium text-shell-text-tertiary mb-1">Run this command to install Docker:</p>
              <code className="text-[11px] text-blue-300 font-mono break-all block whitespace-pre-wrap">{command}</code>
            </div>
          )}
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
            {submitting ? "Installing Docker…" : "Agree & Install Docker"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default DockerConsentDialog;