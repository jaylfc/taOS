import { useState, useEffect, useCallback, useRef } from "react";
import {
  Layers,
  Plus,
  Loader2,
  AlertTriangle,
  Trash2,
  RotateCcw,
  ExternalLink,
  Check,
  Copy,
} from "lucide-react";
import {
  listLoras,
  ingestLora,
  deleteLora,
  retryLora,
  loraPreviewUrl,
  type LoraItem,
} from "@/lib/loras";

/* ------------------------------------------------------------------ */
/*  LoRA Studio                                                        */
/*                                                                     */
/*  Grid of ingested LoRAs (Civitai-sourced .safetensors adapters) +   */
/*  a detail panel, plus an Add-by-URL form. Mirrors VideoStudioApp's  */
/*  Library view shape (see videostudio/LibraryView.tsx). The list     */
/*  auto-refreshes while any row is pending/downloading -- taOS design */
/*  law: live surfaces refresh themselves.                             */
/* ------------------------------------------------------------------ */

const POLL_INTERVAL_MS = 2000;

function formatBytes(b: number): string {
  if (!b) return "—";
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1024 * 1024 * 1024) return `${(b / (1024 * 1024)).toFixed(1)} MB`;
  return `${(b / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function loraType(item: LoraItem): string | null {
  const t = item.meta_json?.type;
  return typeof t === "string" && t ? t : null;
}

function isActive(item: LoraItem): boolean {
  return item.status === "pending" || item.status === "downloading";
}

function Skeleton() {
  return (
    <div
      className="taos-shimmer aspect-square rounded-2xl border border-shell-border bg-shell-surface-active"
      aria-hidden="true"
    />
  );
}

function StatusBadge({ status }: { status: LoraItem["status"] }) {
  const styles: Record<LoraItem["status"], string> = {
    pending: "border-amber-500/30 bg-amber-500/10 text-amber-400",
    downloading: "border-blue-500/30 bg-blue-500/10 text-blue-400",
    ready: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
    failed: "border-red-500/30 bg-red-500/10 text-red-400",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-medium capitalize ${styles[status]}`}
    >
      {isActive({ status } as LoraItem) && <Loader2 size={9} className="animate-spin" />}
      {status}
    </span>
  );
}

function TriggerWordChip({ word }: { word: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(word);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      /* no-op */
    }
  }, [word]);
  return (
    <button
      type="button"
      onClick={() => void copy()}
      aria-label={`Copy trigger word ${word}`}
      className="inline-flex items-center gap-1 rounded-full border border-shell-border bg-shell-surface px-2 py-0.5 text-[11px] text-shell-text-secondary transition-colors hover:border-accent/40 hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
    >
      {copied ? <Check size={10} /> : <Copy size={10} />}
      {word}
    </button>
  );
}

export function LoRAStudioApp({ windowId: _windowId }: { windowId: string }) {
  const [items, setItems] = useState<LoraItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const [urlInput, setUrlInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /* ------------------------------- list ---------------------------- */

  const fetchLoras = useCallback(async () => {
    try {
      const list = await listLoras();
      setItems(list);
      setListError(null);
    } catch (e) {
      setListError(`Failed to load LoRAs: ${(e as Error).message}`);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchLoras();
  }, [fetchLoras]);

  // Live-refresh while anything is still downloading/pending.
  useEffect(() => {
    const anyActive = items.some(isActive);
    if (!anyActive) {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
      return;
    }
    if (pollTimerRef.current) return;
    pollTimerRef.current = setInterval(fetchLoras, POLL_INTERVAL_MS);
    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [items, fetchLoras]);

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, []);

  const selected = items.find((i) => i.id === selectedId) ?? null;

  /* ------------------------------- add ------------------------------ */

  const handleAdd = useCallback(async () => {
    const url = urlInput.trim();
    if (!url || adding) return;
    setAdding(true);
    setAddError(null);
    const created = await ingestLora(url);
    setAdding(false);
    if (!created) {
      setAddError("Failed to add LoRA. Check the URL and try again.");
      return;
    }
    setUrlInput("");
    setItems((prev) => [created, ...prev.filter((i) => i.id !== created.id)]);
    setSelectedId(created.id);
  }, [urlInput, adding]);

  /* ------------------------------ delete ----------------------------- */

  const handleDelete = useCallback(async (id: string) => {
    if (!window.confirm("Delete this LoRA? This can't be undone.")) return;
    const ok = await deleteLora(id);
    if (!ok) {
      setListError("Failed to delete LoRA.");
      return;
    }
    setItems((prev) => prev.filter((i) => i.id !== id));
    setSelectedId((cur) => (cur === id ? null : cur));
  }, []);

  /* ------------------------------ retry ------------------------------ */

  const handleRetry = useCallback(async (id: string) => {
    const updated = await retryLora(id);
    if (!updated) {
      setListError("Failed to retry ingest.");
      return;
    }
    // The route returns {id, status} only -- merge it so the card keeps its
    // name, previews, tags, and metadata while the re-ingest runs.
    setItems((prev) =>
      prev.map((i) => (i.id === id ? { ...i, status: updated.status, error: null } : i)),
    );
  }, []);

  /* ------------------------------ render ------------------------------ */

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-shell-bg text-shell-text select-none">
      {/* header */}
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <Layers size={18} className="text-accent" />
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">LoRA Studio</h2>
        <span className="text-[12px] text-shell-text-tertiary">
          {items.length} {items.length === 1 ? "LoRA" : "LoRAs"}
        </span>

        <form
          className="ml-auto flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void handleAdd();
          }}
        >
          <label htmlFor="lora-add-url" className="sr-only">
            Civitai model URL
          </label>
          <input
            id="lora-add-url"
            type="url"
            inputMode="url"
            placeholder="Paste a Civitai model URL…"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            className="h-8 w-[280px] rounded-lg border border-shell-border bg-shell-surface px-2.5 text-[12.5px] text-shell-text placeholder:text-shell-text-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          />
          <button
            type="submit"
            disabled={!urlInput.trim() || adding}
            aria-label="Add LoRA by URL"
            className="flex h-8 items-center gap-1.5 rounded-lg bg-accent px-3 text-[12px] font-semibold text-white transition-all hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            {adding ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
            Add
          </button>
        </form>
      </div>

      {addError && (
        <div role="alert" className="border-b border-red-500/30 bg-red-500/10 px-[22px] py-2 text-xs text-red-400">
          {addError}
        </div>
      )}
      {listError && (
        <div role="alert" className="border-b border-red-500/30 bg-red-500/10 px-[22px] py-2 text-xs text-red-400">
          {listError}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* grid */}
        <div
          aria-label="LoRA library"
          className={`grid flex-1 content-start gap-3.5 overflow-auto p-[22px] ${
            selected ? "grid-cols-2" : "grid-cols-4"
          }`}
        >
          {loading ? (
            [0, 1, 2, 3].map((i) => <Skeleton key={i} />)
          ) : items.length === 0 ? (
            // The empty state claims the archive IS empty, so it must not
            // stand in for a failed load -- the error banner says that.
            listError ? null : (
              <div className="col-span-full flex flex-col items-center justify-center gap-2 py-16 text-shell-text-tertiary">
                <Layers size={40} className="opacity-30" />
                <p className="text-sm">No LoRAs yet</p>
                <p className="text-xs">Paste a Civitai model URL above to ingest one.</p>
              </div>
            )
          ) : (
            items.map((item) => {
              const sel = item.id === selectedId;
              const type = loraType(item);
              const preview = item.preview_paths?.[0]
                ? loraPreviewUrl(item.id, 0)
                : null;
              return (
                <button
                  key={item.id}
                  type="button"
                  aria-label={`Open LoRA: ${item.name || item.id}`}
                  aria-pressed={sel}
                  onClick={() => setSelectedId(item.id)}
                  className={`group relative flex flex-col overflow-hidden rounded-2xl border text-left transition-all duration-200 hover:-translate-y-0.5 hover:shadow-[var(--shadow-card-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                    sel
                      ? "border-accent ring-2 ring-accent/30"
                      : "border-shell-border hover:border-shell-border-strong"
                  }`}
                >
                  <div className="aspect-square w-full overflow-hidden bg-shell-bg-deep">
                    {preview ? (
                      <img
                        src={preview}
                        alt={`Preview of ${item.name || "LoRA"}`}
                        className="h-full w-full object-cover"
                      />
                    ) : (
                      <span className="flex h-full w-full items-center justify-center text-shell-text-tertiary">
                        <Layers size={24} />
                      </span>
                    )}
                  </div>
                  <div className="flex flex-1 flex-col gap-1.5 p-2.5">
                    <div className="flex items-center justify-between gap-1.5">
                      <span className="line-clamp-1 text-[12.5px] font-semibold">
                        {item.name || item.id}
                      </span>
                      <StatusBadge status={item.status} />
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {item.base_model && (
                        <span className="rounded-full border border-shell-border bg-shell-surface px-1.5 py-0.5 text-[10px] text-shell-text-secondary">
                          {item.base_model}
                        </span>
                      )}
                      {type && (
                        <span className="rounded-full border border-shell-border bg-shell-surface px-1.5 py-0.5 text-[10px] text-shell-text-secondary">
                          {type}
                        </span>
                      )}
                    </div>
                    {item.tags?.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {item.tags.slice(0, 3).map((tag) => (
                          <span
                            key={tag}
                            className="rounded-full bg-white/5 px-1.5 py-0.5 text-[10px] text-shell-text-tertiary"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                    {item.status === "failed" && item.error && (
                      <p className="mt-1 flex items-start gap-1 text-[10.5px] leading-snug text-red-400">
                        <AlertTriangle size={11} className="mt-0.5 shrink-0" />
                        <span className="line-clamp-2">{item.error}</span>
                      </p>
                    )}
                  </div>
                </button>
              );
            })
          )}
        </div>

        {/* detail pane */}
        {selected && (
          <div className="flex w-[340px] flex-none flex-col gap-4 overflow-auto border-l border-shell-border p-[18px]">
            <div className="aspect-square w-full overflow-hidden rounded-2xl border border-shell-border bg-shell-bg-deep">
              {selected.preview_paths?.[0] ? (
                <img
                  src={loraPreviewUrl(selected.id, 0)}
                  alt={`Preview of ${selected.name || "LoRA"}`}
                  className="h-full w-full object-cover"
                />
              ) : (
                <span className="flex h-full w-full items-center justify-center text-shell-text-tertiary">
                  <Layers size={28} />
                </span>
              )}
            </div>

            {selected.preview_paths && selected.preview_paths.length > 1 && (
              <div className="flex gap-2 overflow-x-auto">
                {selected.preview_paths.map((_, idx) => (
                  <img
                    key={idx}
                    src={loraPreviewUrl(selected.id, idx)}
                    alt={`Preview ${idx + 1} of ${selected.name || "LoRA"}`}
                    className="h-14 w-14 flex-none rounded-lg border border-shell-border object-cover"
                  />
                ))}
              </div>
            )}

            <div className="flex items-center gap-2">
              <h3 className="flex-1 text-sm font-bold tracking-[-0.01em] line-clamp-1">
                {selected.name || selected.id}
              </h3>
              <StatusBadge status={selected.status} />
            </div>

            {selected.status === "failed" && selected.error && (
              <div role="alert" className="flex items-start gap-2 rounded-xl border border-red-500/30 bg-red-500/10 p-2.5 text-[12px] leading-relaxed text-red-400">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                <span>{selected.error}</span>
              </div>
            )}

            {selected.description && (
              <p className="text-[12.5px] leading-relaxed text-shell-text-secondary">
                {selected.description}
              </p>
            )}

            {selected.trigger_words?.length > 0 && (
              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] font-semibold text-shell-text-tertiary">
                  Trigger words
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {selected.trigger_words.map((w) => (
                    <TriggerWordChip key={w} word={w} />
                  ))}
                </div>
              </div>
            )}

            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between text-[11.5px]">
                <span className="text-shell-text-tertiary">Creator</span>
                <b className="font-semibold text-shell-text">{selected.creator || "—"}</b>
              </div>
              <div className="flex items-center justify-between text-[11.5px]">
                <span className="text-shell-text-tertiary">Base model</span>
                <b className="font-semibold text-shell-text">{selected.base_model || "—"}</b>
              </div>
              <div className="flex items-center justify-between text-[11.5px]">
                <span className="text-shell-text-tertiary">File size</span>
                <b className="font-semibold tabular-nums text-shell-text">{formatBytes(selected.bytes)}</b>
              </div>
              <div className="flex items-start justify-between gap-2 text-[11.5px]">
                <span className="shrink-0 text-shell-text-tertiary">SHA-256</span>
                <b className="break-all text-right font-mono text-[10.5px] font-medium text-shell-text">
                  {selected.sha256 || "—"}
                </b>
              </div>
            </div>

            <div className="flex gap-2">
              {selected.source_url && (
                <a
                  href={selected.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label="Open source on Civitai"
                  className="flex h-9 flex-1 items-center justify-center gap-1.5 rounded-xl border border-transparent bg-accent text-[11.5px] font-semibold text-white transition-all hover:brightness-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                >
                  <ExternalLink size={14} />
                  Source
                </a>
              )}
              {selected.status === "failed" && (
                <button
                  type="button"
                  onClick={() => void handleRetry(selected.id)}
                  aria-label="Retry ingest"
                  className="flex h-9 w-9 items-center justify-center rounded-xl border border-shell-border bg-shell-surface text-shell-text transition-colors hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                >
                  <RotateCcw size={14} />
                </button>
              )}
              <button
                type="button"
                onClick={() => void handleDelete(selected.id)}
                aria-label="Delete LoRA"
                className="flex h-9 w-9 items-center justify-center rounded-xl border border-shell-border bg-shell-surface text-shell-text transition-colors hover:bg-red-500/15 hover:text-red-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
