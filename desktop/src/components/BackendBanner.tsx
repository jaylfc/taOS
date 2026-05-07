/**
 * Top-of-viewport banner shown while the backend is unreachable.
 *
 * Copy is deliberately non-technical:
 *   - first 60s: "taOS is restarting…"
 *   - after 60s: "taOS is taking longer than usual." + [Refresh page]
 *
 * Hidden when status === "up". Has aria-live so screen readers announce
 * the state change without the user needing focus to be in the banner.
 */
import { Loader2, RefreshCw } from "lucide-react";
import { useBackendStatus } from "@/contexts/BackendStatusContext";

const LONG_THRESHOLD_S = 60;

export function BackendBanner() {
  const { status, secondsReconnecting } = useBackendStatus();
  const takingLong = secondsReconnecting >= LONG_THRESHOLD_S;
  const message = takingLong
    ? "taOS is taking longer than usual."
    : "taOS is restarting…";

  // Always render the live region (empty when up) so screen readers attach to
  // it before content arrives. Visible chrome is conditional on status.
  return (
    <div role="status" aria-live="polite">
      {status !== "up" && (
        // z-[9500]: above app chrome / Launchpad (~9000), below toasts/modals (10000+).
        // pt is max(0.5rem, safe-area-top) so the spinner + text clear the
        // iOS notch / Android status bar when running as an installed PWA;
        // on desktop env(safe-area-inset-top)=0 and the original padding
        // baseline applies.
        <div className="fixed top-0 left-0 right-0 z-[9500] flex items-center justify-center gap-3 bg-amber-500/95 px-4 pb-2 pt-[max(0.5rem,env(safe-area-inset-top))] text-sm font-medium text-amber-950 shadow-md">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          <span>{message}</span>
          {takingLong && (
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="ml-3 inline-flex items-center gap-1.5 rounded bg-amber-950/15 px-2.5 py-1 text-xs font-semibold hover:bg-amber-950/25"
            >
              <RefreshCw className="h-3 w-3" aria-hidden="true" />
              Refresh page
            </button>
          )}
        </div>
      )}
    </div>
  );
}
