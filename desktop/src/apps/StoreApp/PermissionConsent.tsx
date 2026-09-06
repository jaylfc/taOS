import { useCallback, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useFocusTrap } from "@/hooks/use-focus-trap";
import { grantUserspacePermissions } from "@/lib/userspace-apps";

/**
 * Capabilities that require explicit user consent before an app can use
 * them. Mirrors tinyagentos/userspace/capabilities.py GATED_CAPS exactly --
 * keep in sync if the backend vocabulary changes.
 */
export const GATED_CAPS = new Set(["app.net", "app.agent", "app.llm", "app.memory"]);

/**
 * One-line, human descriptions per capability, for the consent dialog.
 * Mirrors tinyagentos/userspace/capabilities.py CAPABILITY_DESCRIPTIONS.
 */
const CAPABILITY_DESCRIPTIONS: Record<string, string> = {
  "app.kv": "Store and read its own app data",
  "app.table": "Store and read its own structured data",
  "app.files": "Read and write files in its own app folder",
  "app.notify": "Send you notifications",
  "app.window": "Open and manage its own windows",
  "app.net": "Connect to the internet",
  "app.agent": "Ask your taOS agent for help",
  "app.llm": "Use a language model",
  "app.memory": "Read and write your memories",
};

/** A human one-liner for a capability. Mirrors describe_capability() in
 * capabilities.py: a `network:<origin>` grant renders as "Connect to
 * <origin>"; anything unrecognised falls back to the raw token. */
function describeCapability(cap: string): string {
  if (cap.startsWith("network:")) return `Connect to ${cap.slice("network:".length)}`;
  return CAPABILITY_DESCRIPTIONS[cap] ?? cap;
}

interface Props {
  appId: string;
  appName: string;
  /** The permissions this dialog is asking about (typically new_permissions
   * from the install response). */
  requested: string[];
  /** Permissions already granted before this consent, preserved on Allow so
   * a re-consent (update) never revokes an earlier grant. */
  alreadyGranted?: string[];
  /** Called after the granted permissions have been POSTed. */
  onAllow: (granted: string[]) => void;
  /** Called when the user denies or dismisses the dialog; no new permission
   * is granted. */
  onDeny: () => void;
}

export function PermissionConsent({
  appId,
  appName,
  requested,
  alreadyGranted = [],
  onAllow,
  onDeny,
}: Props) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef, true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleAllow = useCallback(async () => {
    setSubmitting(true);
    setError(null);
    try {
      const granted = Array.from(new Set([...alreadyGranted, ...requested]));
      await grantUserspacePermissions(appId, granted);
      onAllow(granted);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      setSubmitting(false);
    }
  }, [appId, alreadyGranted, requested, onAllow]);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={(e) => {
        if (submitting) return;
        if (e.target === e.currentTarget) onDeny();
      }}
      onKeyDown={(e) => {
        if (submitting) return;
        if (e.key === "Escape") onDeny();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={`Permissions for ${appName}`}
        className="bg-shell-surface border border-white/10 shadow-2xl rounded-2xl w-[26rem] max-w-[90vw] flex flex-col"
      >
        <div className="px-5 py-4 border-b border-white/5">
          <h2 className="text-sm font-semibold text-shell-text">
            {appName} wants permission to:
          </h2>
        </div>
        <ul className="px-5 py-4 space-y-2.5">
          {requested.map((cap) => {
            const sensitive = GATED_CAPS.has(cap);
            return (
              <li key={cap} className="flex items-start gap-2 text-[13px]">
                {sensitive && (
                  <span aria-hidden className="text-amber-400 leading-tight">
                    ⚠
                  </span>
                )}
                <span
                  className={
                    sensitive
                      ? "text-shell-text font-medium leading-tight"
                      : "text-shell-text-secondary leading-tight"
                  }
                >
                  {describeCapability(cap)}
                </span>
              </li>
            );
          })}
        </ul>
        {error && (
          <div role="alert" className="mx-5 mb-3 text-[12px] text-red-300 bg-red-500/10 border border-red-500/20 rounded px-2 py-1">
            {error}
          </div>
        )}
        <div className="flex justify-end gap-2 px-5 py-4 border-t border-white/5">
          <button
            type="button"
            onClick={onDeny}
            disabled={submitting}
            className="px-4 py-1.5 rounded-full text-[13px] font-semibold text-shell-text-secondary hover:text-shell-text hover:bg-white/[0.04] transition-colors disabled:opacity-60"
          >
            Deny
          </button>
          <button
            type="button"
            onClick={handleAllow}
            disabled={submitting}
            className="px-4 py-1.5 rounded-full text-[13px] font-bold text-white transition-colors disabled:opacity-60"
            style={{ background: "var(--color-accent)" }}
          >
            {submitting ? "Allowing…" : "Allow"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default PermissionConsent;
