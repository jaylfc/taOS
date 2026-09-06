import { useCallback, useEffect, useState } from "react";
import {
  Lock,
  Monitor,
  Tablet,
  Smartphone,
  RotateCcw,
  Loader2,
  AlertCircle,
} from "lucide-react";

type DeviceMode = "desktop" | "tablet" | "phone";

const DEVICE_WIDTHS: Record<DeviceMode, string> = {
  desktop: "100%",
  tablet: "834px",
  phone: "390px",
};

export function PreviewView({
  workspaceId,
  workspaceName,
}: {
  workspaceId: string | null;
  workspaceName?: string;
}) {
  const [deviceMode, setDeviceMode] = useState<DeviceMode>("desktop");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [noIndex, setNoIndex] = useState(false);
  const [html, setHtml] = useState<string | null>(null);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!workspaceId) {
        setHtml(null);
        setNoIndex(false);
        setError(null);
        return;
      }
      setLoading(true);
      setError(null);
      setNoIndex(false);
      try {
        const res = await fetch(`/api/coding/workspaces/${workspaceId}/preview`, { signal });
        if (res.status === 404) {
          const data = await res.json().catch(() => ({}));
          if ((data as { error?: string }).error === "no_index") {
            setNoIndex(true);
            setHtml(null);
            return;
          }
          throw new Error(`HTTP ${res.status}`);
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const text = await res.text();
        setHtml(text);
      } catch (err) {
        // A superseded request (workspace switched, or Refresh re-fired) aborts
        // the in-flight fetch; ignore it so it can't overwrite fresher state.
        if (signal?.aborted || (err instanceof DOMException && err.name === "AbortError")) {
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load preview");
        setHtml(null);
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [workspaceId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div
        className="flex flex-none items-center gap-3 border-b border-shell-border px-[22px]"
        style={{ height: "54px" }}
      >
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Preview</h2>
        {workspaceName && (
          <span className="text-[12px] text-shell-text-tertiary">{workspaceName}</span>
        )}
      </div>

      {/* preview stage */}
      <div className="flex min-h-0 flex-1 flex-col p-[18px]">
        {/* url bar + device toggle */}
        <div className="mb-3.5 flex flex-none items-center gap-2.5">
          <div className="flex h-[34px] flex-1 items-center gap-2.5 rounded-[10px] border border-shell-border bg-shell-surface px-3 text-[12px] text-shell-text-tertiary">
            <Lock size={13} className="text-green-400" />
            {workspaceName ?? "no workspace selected"}
          </div>
          <div className="flex gap-0 rounded-full border border-shell-border bg-shell-surface p-[3px]">
            {(
              [
                { id: "desktop" as DeviceMode, Icon: Monitor },
                { id: "tablet" as DeviceMode, Icon: Tablet },
                { id: "phone" as DeviceMode, Icon: Smartphone },
              ] as { id: DeviceMode; Icon: typeof Monitor }[]
            ).map(({ id, Icon }) => (
              <button
                key={id}
                type="button"
                aria-label={id}
                aria-pressed={deviceMode === id}
                onClick={() => setDeviceMode(id)}
                className={`flex cursor-pointer items-center rounded-full px-[11px] py-[5px] ${
                  deviceMode === id
                    ? "bg-shell-surface-active text-shell-text"
                    : "text-shell-text-tertiary"
                }`}
              >
                <Icon size={15} />
              </button>
            ))}
          </div>
        </div>

        {/* frame */}
        <div className="flex flex-1 justify-center overflow-hidden">
          <div
            data-testid="preview-frame"
            className="flex h-full flex-col overflow-hidden rounded-[16px] border border-shell-border-strong bg-white"
            style={{ width: DEVICE_WIDTHS[deviceMode], maxWidth: "100%" }}
          >
            {!workspaceId ? (
              <div className="flex flex-1 items-center justify-center bg-shell-surface p-6 text-center text-[12.5px] text-shell-text-tertiary">
                Select or create a workspace to preview it.
              </div>
            ) : loading ? (
              <div className="flex flex-1 items-center justify-center gap-2 bg-shell-surface p-6 text-[12.5px] text-shell-text-tertiary">
                <Loader2 size={15} className="animate-spin" />
                Loading preview...
              </div>
            ) : noIndex ? (
              <div className="flex flex-1 items-center justify-center bg-shell-surface p-6 text-center text-[12.5px] text-shell-text-tertiary">
                No index.html to preview. Add one to this workspace, or use Build to generate a
                site.
              </div>
            ) : error ? (
              <div className="flex flex-1 items-center justify-center gap-2 bg-shell-surface p-6 text-center text-[12.5px] text-red-400">
                <AlertCircle size={15} />
                {error}
              </div>
            ) : html !== null ? (
              <iframe
                sandbox="allow-scripts"
                srcDoc={html}
                title="Preview"
                className="h-full w-full border-0"
              />
            ) : null}
          </div>
        </div>
      </div>

      {/* footer bar */}
      <div className="flex flex-none items-center gap-2.5 border-t border-shell-border bg-shell-bg-deep px-[18px] py-3.5">
        <button
          type="button"
          onClick={() => void load()}
          disabled={!workspaceId || loading}
          className="flex h-10 cursor-pointer items-center gap-2 rounded-[12px] border border-shell-border bg-shell-surface px-4 text-[12.5px] font-semibold hover:bg-shell-surface-active disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <RotateCcw size={15} />}
          Refresh
        </button>
      </div>
    </div>
  );
}
