import { useState } from "react";
import { CloudOff, Plane, RefreshCw } from "lucide-react";

interface Props {
  /** Re-run the host reachability check. Should resolve when the check completes. */
  onRetry: () => Promise<void> | void;
}

/**
 * Shown when the taOS host cannot be reached from the current network (e.g. the
 * PWA was opened off the host's LAN). Rather than load the shell into a broken,
 * data-less state, we offer the way back in: taOSgo for secure access from
 * anywhere, plus a retry for a transient blip.
 */
export function OffNetworkScreen({ onRetry }: Props) {
  const [checking, setChecking] = useState(false);

  const retry = async () => {
    setChecking(true);
    try {
      await onRetry();
    } finally {
      setChecking(false);
    }
  };

  return (
    <div
      className="h-screen w-screen flex items-center justify-center p-4"
      style={{ background: "var(--color-shell-bg)" }}
    >
      <div
        className="w-full max-w-sm p-6 rounded-2xl border border-white/10 text-center"
        style={{ backgroundColor: "rgba(255,255,255,0.04)", backdropFilter: "blur(20px)" }}
      >
        <div
          className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4"
          style={{ background: "linear-gradient(135deg, #8b92a3, #5b6170)" }}
        >
          <CloudOff size={24} className="text-white" />
        </div>

        <h1 className="text-lg font-semibold text-shell-text">Can't reach your taOS</h1>
        <p className="text-sm text-shell-text-secondary mt-2 leading-relaxed">
          Your taOS isn't reachable from this network. taOSgo gives you secure access
          from anywhere, with nothing to install.
        </p>

        <a
          href="https://taos.my/taosgo"
          className="mt-5 w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-accent text-white text-sm font-medium hover:brightness-110 transition-all"
        >
          <Plane size={15} /> Get taOSgo
        </a>

        <button
          type="button"
          onClick={retry}
          disabled={checking}
          className="mt-2 w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg border border-white/10 text-sm text-shell-text hover:bg-white/5 disabled:opacity-50 transition-colors"
        >
          <RefreshCw size={14} className={checking ? "animate-spin" : ""} />
          {checking ? "Checking..." : "Try again"}
        </button>

        <p className="text-xs text-shell-text-tertiary mt-4">
          On your own Tailscale network? Reach your taOS through your tailnet.
        </p>
      </div>
    </div>
  );
}
