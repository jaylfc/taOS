import { useState, useEffect, useCallback } from "react";
import {
  ChevronRight,
  Layers,
  RefreshCw,
  DownloadCloud,
  Trash2,
  HardDrive,
} from "lucide-react";
import { Button, Card } from "@/components/ui";
import {
  parseBaseImagesResponse,
  formatBytes,
  frameworkLabel,
  type BaseImageRow,
  type BaseImagesView,
} from "./baseImages";

/* ------------------------------------------------------------------ */
/*  Base Images management pane                                         */
/*                                                                     */
/*  Advanced / secondary surface (most users never need it): manage    */
/*  the prebuilt taos base images that let fresh deploys skip the cold  */
/*  apt run. Consumes /api/agent-images (list / import / delete /       */
/*  prefetch). Follows the collapsible-section pattern used by the      */
/*  Registry and Archived panels.                                      */
/* ------------------------------------------------------------------ */

const EMPTY_VIEW: BaseImagesView = {
  images: [],
  totalSizeBytes: 0,
  prefetchEnabled: false,
  incusAvailable: false,
};

function BaseImageRowItem({
  row,
  busy,
  onImport,
  onPrune,
}: {
  row: BaseImageRow;
  busy: boolean;
  onImport: (alias: string) => void;
  onPrune: (row: BaseImageRow) => void;
}) {
  return (
    <Card className="flex items-center gap-3 px-4 py-3" role="listitem">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-sm truncate">{frameworkLabel(row.framework)}</span>
          <span className="text-[11px] text-shell-text-tertiary">{row.architecture}</span>
          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium border bg-emerald-500/20 text-emerald-300 border-emerald-500/30">
            present
          </span>
        </div>
        <div className="flex items-center gap-3 mt-0.5 flex-wrap">
          <code
            className="text-[10px] text-shell-text-tertiary font-mono truncate max-w-[220px]"
            title={row.alias}
          >
            {row.alias}
          </code>
          <span className="text-[11px] text-shell-text-tertiary">{row.size || formatBytes(row.sizeBytes)}</span>
          {row.uploadedAt && (
            <span className="text-[11px] text-shell-text-tertiary">built {row.uploadedAt}</span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-1 shrink-0" role="group" aria-label="Base image actions">
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 hover:bg-accent/15 hover:text-accent"
          onClick={() => onImport(row.alias)}
          disabled={busy}
          aria-label={`Re-import ${frameworkLabel(row.framework)} base image`}
          title="Re-import / refresh"
        >
          <DownloadCloud size={14} />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 hover:bg-red-500/15 hover:text-red-400"
          onClick={() => onPrune(row)}
          disabled={busy}
          aria-label={`Prune ${frameworkLabel(row.framework)} base image`}
          title="Prune to reclaim disk"
        >
          <Trash2 size={14} />
        </Button>
      </div>
    </Card>
  );
}

export function BaseImagesPanel() {
  const [expanded, setExpanded] = useState(false);
  const [view, setView] = useState<BaseImagesView>(EMPTY_VIEW);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Alias currently running an action, so its row buttons disable.
  const [busyAlias, setBusyAlias] = useState<string | null>(null);
  const [prefetchBusy, setPrefetchBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const resp = await fetch("/api/agent-images", { credentials: "include" });
      if (!resp.ok) {
        setErr(`Failed to load base images (${resp.status})`);
        return;
      }
      setView(parseBaseImagesResponse(await resp.json()));
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Network error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (expanded) load();
  }, [expanded, load]);

  async function handleImport(alias: string) {
    setBusyAlias(alias);
    setErr(null);
    setNotice(null);
    try {
      const resp = await fetch(`/api/agent-images/${encodeURIComponent(alias)}/import`, {
        method: "POST",
        credentials: "include",
      });
      if (!resp.ok) {
        const e = await resp.json().catch(() => ({}));
        setErr((e as { detail?: string }).detail ?? `Import failed (${resp.status})`);
        return;
      }
      setNotice(`Imported ${alias}.`);
      await load();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Network error");
    } finally {
      setBusyAlias(null);
    }
  }

  async function handlePrune(row: BaseImageRow) {
    const label = frameworkLabel(row.framework);
    const confirmed = window.confirm(
      `Prune the ${label} base image (${row.alias})?\n\n` +
        "This is safe: base images are reproducible and a container's rootfs is " +
        "copied at launch, so pruning never affects a running agent -- only future " +
        "cold deploys, which fall back to the slower uncached path. You can re-import " +
        "it any time.",
    );
    if (!confirmed) return;

    setBusyAlias(row.alias);
    setErr(null);
    setNotice(null);
    try {
      const resp = await fetch(`/api/agent-images/${encodeURIComponent(row.alias)}`, {
        method: "DELETE",
        credentials: "include",
      });
      if (!resp.ok) {
        const e = await resp.json().catch(() => ({}));
        setErr((e as { detail?: string }).detail ?? `Prune failed (${resp.status})`);
        return;
      }
      setNotice(`Pruned ${row.alias}.`);
      await load();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Network error");
    } finally {
      setBusyAlias(null);
    }
  }

  async function handlePrefetchToggle(enabled: boolean) {
    setPrefetchBusy(true);
    setErr(null);
    setNotice(null);
    // Optimistic: reflect the intent immediately, reconcile from the response.
    setView((v) => ({ ...v, prefetchEnabled: enabled }));
    try {
      const resp = await fetch("/api/agent-images/prefetch", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!resp.ok) {
        const e = await resp.json().catch(() => ({}));
        setErr((e as { detail?: string }).detail ?? `Prefetch toggle failed (${resp.status})`);
        setView((v) => ({ ...v, prefetchEnabled: !enabled }));
        return;
      }
      const data = await resp.json();
      setView((v) => ({ ...v, prefetchEnabled: Boolean(data.prefetch_enabled) }));
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Network error");
      setView((v) => ({ ...v, prefetchEnabled: !enabled }));
    } finally {
      setPrefetchBusy(false);
    }
  }

  return (
    <section className="mt-4" aria-label="Base images">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-2 text-xs text-shell-text-secondary hover:text-shell-text transition-colors mb-2 w-full"
        aria-expanded={expanded}
        aria-controls="base-images-panel"
      >
        <ChevronRight
          size={14}
          className={`transition-transform shrink-0 ${expanded ? "rotate-90" : ""}`}
          aria-hidden
        />
        <Layers size={13} aria-hidden />
        <span>Base Images</span>
        <span className="text-shell-text-tertiary">(advanced)</span>
      </button>

      <div id="base-images-panel" className={`space-y-3 ${expanded ? "" : "hidden"}`}>
        <p className="text-[11px] text-shell-text-tertiary">
          Prebuilt base images let fresh deploys skip the cold setup run. Most users never
          need to touch these.
        </p>

        {/* Aggregate disk usage + prefetch toggle */}
        <Card className="flex items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-2 min-w-0">
            <HardDrive size={16} className="text-accent shrink-0" aria-hidden />
            <div className="min-w-0">
              <div className="text-sm font-semibold">
                {formatBytes(view.totalSizeBytes)}
              </div>
              <div className="text-[11px] text-shell-text-tertiary">
                total base-image disk usage
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-[11px] text-shell-text-secondary">Prefetch on boot</span>
            <Button
              variant="outline"
              size="sm"
              role="switch"
              aria-checked={view.prefetchEnabled}
              aria-label="Prefetch base images on boot"
              onClick={() => handlePrefetchToggle(!view.prefetchEnabled)}
              disabled={prefetchBusy}
              className={view.prefetchEnabled ? "border-accent/40 text-accent" : ""}
            >
              {view.prefetchEnabled ? "On" : "Off"}
            </Button>
          </div>
        </Card>

        {notice && (
          <p className="text-[11px] text-emerald-400" role="status">{notice}</p>
        )}
        {err && (
          <p className="text-[11px] text-red-400" role="alert">{err}</p>
        )}

        {loading ? (
          <div className="flex items-center gap-2 text-xs text-shell-text-tertiary py-2">
            <RefreshCw size={12} className="animate-spin" aria-hidden />
            Loading base images…
          </div>
        ) : !view.incusAvailable ? (
          <p className="text-xs text-shell-text-tertiary py-1">
            The container runtime is unavailable on this host, so no base images can be
            listed or managed.
          </p>
        ) : view.images.length === 0 ? (
          <p className="text-xs text-shell-text-tertiary py-1">
            No base images present. Import one to speed up future deploys.
          </p>
        ) : (
          <div className="space-y-2" role="list" aria-label="Base image list">
            {view.images.map((row) => (
              <BaseImageRowItem
                key={row.alias}
                row={row}
                busy={busyAlias === row.alias}
                onImport={handleImport}
                onPrune={handlePrune}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
