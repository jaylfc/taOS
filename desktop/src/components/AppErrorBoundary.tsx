/**
 * Top-level React error boundary.
 *
 * Catches two error types with non-technical-friendly fallbacks:
 *  - BackendUnavailableError (from taos-fetch during reconnecting):
 *    inline skeleton with "Waiting for taOS to come back…" — apps
 *    without their own error states fall through to this.
 *  - ChunkLoadError (stale shell after a deploy points at a deleted
 *    asset): full friendly reload page with auto-reload after 5s.
 *
 * All other errors render a minimal "Something went wrong" line.
 * (We don't pretend to handle arbitrary failures; that's the job of
 * the surrounding observability stack and per-app error states.)
 */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { Loader2, RefreshCw, AlertCircle } from "lucide-react";
import { BackendUnavailableError } from "@/lib/taos-fetch";

type Mode = "ok" | "waiting" | "chunk" | "generic";

interface Props {
  children: ReactNode;
}

interface State {
  mode: Mode;
}

const CHUNK_NAMES = new Set(["ChunkLoadError"]);
const CHUNK_MESSAGES = [/Loading chunk \d+ failed/i, /Failed to fetch dynamically imported module/i];

function classify(err: Error): Mode {
  if (err instanceof BackendUnavailableError) return "waiting";
  if (CHUNK_NAMES.has(err.name)) return "chunk";
  if (CHUNK_MESSAGES.some((re) => re.test(err.message))) return "chunk";
  return "generic";
}

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { mode: "ok" };
  private reloadTimer: ReturnType<typeof setTimeout> | null = null;

  static getDerivedStateFromError(err: Error): State {
    return { mode: classify(err) };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // The generic fallback intentionally hides the error from the user, but it
    // must not be swallowed: log the error and the component stack so a crash
    // is diagnosable from the console and reachable by the observability stack.
    // Skip the "waiting"/"chunk" modes -- those are expected, handled states.
    if (this.state.mode === "generic") {
      console.error("[AppErrorBoundary]", error, info?.componentStack ?? "");
    }
    if (this.state.mode === "chunk" && !this.reloadTimer) {
      this.reloadTimer = setTimeout(() => window.location.reload(), 5_000);
    }
  }

  componentDidUpdate(_: Props, prev: State) {
    if (this.state.mode === "chunk" && prev.mode !== "chunk" && !this.reloadTimer) {
      this.reloadTimer = setTimeout(() => window.location.reload(), 5_000);
    }
  }

  componentWillUnmount() {
    if (this.reloadTimer) clearTimeout(this.reloadTimer);
  }

  render() {
    const { mode } = this.state;
    if (mode === "ok") return this.props.children;
    if (mode === "waiting") {
      return (
        <div role="status" aria-live="polite" className="flex h-full w-full items-center justify-center p-8">
          <div className="flex flex-col items-center gap-3 text-zinc-300">
            <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
            <p className="text-sm">Waiting for taOS to come back…</p>
          </div>
        </div>
      );
    }
    if (mode === "chunk") {
      return (
        <div className="fixed inset-0 z-[10000] flex items-center justify-center bg-zinc-900/95 p-8">
          <div className="flex max-w-md flex-col items-center gap-4 rounded-lg bg-zinc-800 p-6 text-center text-zinc-100">
            <RefreshCw className="h-8 w-8 text-amber-400" aria-hidden="true" />
            <h2 className="text-lg font-semibold">taOS was updated</h2>
            <p className="text-sm text-zinc-300">
              Click below to load the new version. This page will reload automatically in a few seconds.
            </p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded bg-amber-500 px-4 py-2 text-sm font-medium text-amber-950 hover:bg-amber-400"
            >
              Reload
            </button>
          </div>
        </div>
      );
    }
    // generic
    return (
      <div className="flex h-full w-full items-center justify-center p-8">
        <div className="flex items-center gap-2 text-sm text-red-400">
          <AlertCircle className="h-4 w-4" aria-hidden="true" />
          <span>Something went wrong.</span>
        </div>
      </div>
    );
  }
}
