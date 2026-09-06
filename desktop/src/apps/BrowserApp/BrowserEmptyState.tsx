/**
 * BrowserEmptyState — shown in the streamed-view container while a Neko session
 * is starting, or when no tab URL has loaded yet (about:blank / new tab).
 *
 * Two variants:
 *  - "connecting": animated pulse, "Starting full browser session…" copy.
 *  - "new-tab":    clean new-tab prompt with a brief description.
 *
 * This is a pure presentational component — no store reads.
 */
import { MonitorPlay, Globe } from "lucide-react";

interface BrowserEmptyStateProps {
  variant: "connecting" | "new-tab";
}

export function BrowserEmptyState({ variant }: BrowserEmptyStateProps) {
  if (variant === "connecting") {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 bg-shell-bg-deep select-none">
        <span
          className="browser-stream-pulse flex h-[52px] w-[52px] items-center justify-center rounded-2xl bg-shell-surface border border-shell-border text-shell-text-tertiary"
          aria-hidden="true"
        >
          <MonitorPlay size={24} />
        </span>
        <div className="flex flex-col items-center gap-1 text-center">
          <p className="text-[13px] font-semibold text-shell-text">
            Starting full browser session
          </p>
          <p className="text-[11.5px] text-shell-text-tertiary">
            Connecting to a real Chromium instance on your taOS node
          </p>
        </div>
        {/* Subtle loading dots */}
        <span className="flex gap-1.5" aria-label="Loading" role="status">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="h-1.5 w-1.5 rounded-full bg-shell-text-tertiary browser-stream-pulse"
              style={{ animationDelay: `${i * 0.3}s` }}
              aria-hidden="true"
            />
          ))}
        </span>
      </div>
    );
  }

  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-shell-bg-deep select-none">
      <span
        className="flex h-[48px] w-[48px] items-center justify-center rounded-2xl bg-shell-surface border border-shell-border text-shell-text-tertiary"
        aria-hidden="true"
      >
        <Globe size={22} />
      </span>
      <div className="flex flex-col items-center gap-1 text-center">
        <p className="text-[13px] font-semibold text-shell-text">New tab</p>
        <p className="text-[11.5px] text-shell-text-tertiary">
          Type a URL or search in the address bar above
        </p>
      </div>
    </div>
  );
}
