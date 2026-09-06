import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlignLeft,
  ChevronDown,
  ChevronUp,
  Columns2,
  Download,
  Heading1,
  ImagePlus,
  Play,
  Plus,
  Save,
  Square,
  SquareStack,
  Trash2,
  X,
} from "lucide-react";
import {
  blankDeck,
  insertSlideAfter,
  LAYOUTS,
  MAX_IMAGE_BYTES,
  moveSlideBy,
  newSlide,
  parseDeckContent,
  removeSlide,
  serializeDeck,
  updateSlide,
  type Deck,
  type Slide,
  type SlideLayout,
} from "./slides/deck";
import { SlideCanvas } from "./slides/SlideCanvas";
import { PresentMode } from "./slides/PresentMode";
import { exportDeckToPdf, EXPORT_PAGE_HEIGHT, EXPORT_PAGE_WIDTH } from "./slides/pdfExport";

type OfficeDocListItem = {
  id: string;
  kind: string;
  title: string;
  updated_at?: number;
};

type OfficeDoc = OfficeDocListItem & {
  content: string;
};

const LAYOUT_ICONS: Record<SlideLayout, typeof Heading1> = {
  title: Heading1,
  "title-content": AlignLeft,
  "two-column": Columns2,
  "section-header": SquareStack,
  blank: Square,
};

function formatUpdated(ts?: number): string {
  if (!ts) return "Draft";
  const d = new Date(ts * 1000);
  return `Updated ${d.toLocaleString()}`;
}

export function SlidesView() {
  const [docs, setDocs] = useState<OfficeDocListItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [deck, setDeck] = useState<Deck>(() => blankDeck());
  const [selectedSlideId, setSelectedSlideId] = useState<string>(() => deck.slides[0]?.id ?? "");
  const [updatedAt, setUpdatedAt] = useState<number | undefined>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [isPresenting, setIsPresenting] = useState(false);
  const [presentIndex, setPresentIndex] = useState(0);
  const presentStageRef = useRef<HTMLDivElement | null>(null);
  const hadFullscreenRef = useRef(false);

  const exportContainerRef = useRef<HTMLDivElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);

  // deck.slides is never empty: blankDeck/parseDeckContent always seed one
  // slide, and removeSlide refuses to drop the last one.
  const selectedSlide: Slide =
    deck.slides.find((s) => s.id === selectedSlideId) ?? (deck.slides[0] as Slide);

  /* ------------------------------- doc list ------------------------------ */

  const loadList = useCallback(async () => {
    const res = await fetch("/api/office/docs", { credentials: "include" });
    if (!res.ok) throw new Error("Could not load decks");
    const items = (await res.json()) as OfficeDocListItem[];
    setDocs(items.filter((d) => d.kind === "slides"));
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await loadList();
        if (!cancelled) setError(null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Load failed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadList]);

  const applyDeck = useCallback((next: Deck) => {
    setDeck(next);
    setSelectedSlideId(next.slides[0]?.id ?? "");
  }, []);

  const openDoc = async (docId: string) => {
    setError(null);
    try {
      const res = await fetch(`/api/office/docs/${encodeURIComponent(docId)}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Could not open deck");
      const doc = (await res.json()) as OfficeDoc;
      setActiveId(doc.id);
      setUpdatedAt(doc.updated_at);
      applyDeck(parseDeckContent(doc.content));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Open failed");
    }
  };

  const newDoc = () => {
    setActiveId(null);
    setUpdatedAt(undefined);
    setError(null);
    applyDeck(blankDeck());
  };

  const saveDoc = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        kind: "slides",
        title: deck.title.trim() || "Untitled deck",
        content: serializeDeck(deck),
      };
      const url = activeId
        ? `/api/office/docs/${encodeURIComponent(activeId)}`
        : "/api/office/docs";
      const res = await fetch(url, {
        method: activeId ? "PUT" : "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error || "Save failed");
      }
      const saved = (await res.json()) as OfficeDoc;
      setActiveId(saved.id);
      setUpdatedAt(saved.updated_at);
      applyDeck(parseDeckContent(saved.content));
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  /* ------------------------------- slide ops ------------------------------ */

  const patchSelected = useCallback(
    (patch: Partial<Omit<Slide, "id">>) => {
      setDeck((d) => ({ ...d, slides: updateSlide(d.slides, selectedSlideId, patch) }));
    },
    [selectedSlideId],
  );

  const handleAddSlide = useCallback(() => {
    const slide = newSlide();
    setDeck((d) => ({ ...d, slides: insertSlideAfter(d.slides, selectedSlideId, slide) }));
    setSelectedSlideId(slide.id);
  }, [selectedSlideId]);

  const handleDeleteSlide = useCallback(
    (id: string) => {
      if (deck.slides.length <= 1) return;
      const index = deck.slides.findIndex((s) => s.id === id);
      const slides = removeSlide(deck.slides, id);
      setDeck((d) => ({ ...d, slides }));
      if (id === selectedSlideId) {
        const nextIndex = Math.min(index, slides.length - 1);
        setSelectedSlideId(slides[nextIndex]?.id ?? "");
      }
    },
    [deck.slides, selectedSlideId],
  );

  const handleMoveSlide = useCallback((id: string, delta: number) => {
    setDeck((d) => ({ ...d, slides: moveSlideBy(d.slides, id, delta) }));
  }, []);

  /* ------------------------------- image insert ---------------------------- */

  const handleImageFile = useCallback(
    (file: File) => {
      if (!file.type.startsWith("image/")) {
        setError("That file isn't an image. Choose a PNG, JPG, GIF, or WEBP file.");
        return;
      }
      if (file.size > MAX_IMAGE_BYTES) {
        setError(
          `That image is too large (${Math.round(file.size / 1024 / 1024)}MB). Max size is ${MAX_IMAGE_BYTES / 1024 / 1024}MB.`,
        );
        return;
      }
      setError(null);
      const reader = new FileReader();
      reader.onload = () => {
        patchSelected({ imageDataUri: String(reader.result) });
      };
      reader.onerror = () => setError("Could not read that image file.");
      reader.readAsDataURL(file);
    },
    [patchSelected],
  );

  /* ------------------------------- present mode ---------------------------- */

  // Native Fullscreen is best-effort: if the browser grants it we track that
  // in hadFullscreenRef, and only auto-exit present mode if we genuinely had
  // it and it ended (e.g. the user's own Escape or F11). If the browser never
  // grants it (unsupported, or denied), present mode still works windowed —
  // the user is never blocked by a fullscreen failure.
  useEffect(() => {
    const onFsChange = () => {
      const isOurs = document.fullscreenElement === presentStageRef.current;
      if (isOurs) {
        hadFullscreenRef.current = true;
        return;
      }
      if (hadFullscreenRef.current) {
        hadFullscreenRef.current = false;
        setIsPresenting(false);
      }
    };
    document.addEventListener("fullscreenchange", onFsChange);
    return () => document.removeEventListener("fullscreenchange", onFsChange);
  }, []);

  const startPresenting = useCallback(() => {
    const idx = Math.max(0, deck.slides.findIndex((s) => s.id === selectedSlideId));
    setPresentIndex(idx);
    setIsPresenting(true);
    presentStageRef.current?.requestFullscreen?.().catch(() => {});
  }, [deck.slides, selectedSlideId]);

  const exitPresenting = useCallback(() => {
    setIsPresenting(false);
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
  }, []);

  useEffect(() => {
    if (!isPresenting) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        exitPresenting();
      } else if (e.key === "ArrowRight" || e.key === "ArrowDown" || e.key === " ") {
        e.preventDefault();
        setPresentIndex((i) => Math.min(i + 1, deck.slides.length - 1));
      } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        e.preventDefault();
        setPresentIndex((i) => Math.max(i - 1, 0));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isPresenting, exitPresenting, deck.slides.length]);

  /* ------------------------------- export ---------------------------------- */

  const exportPdf = useCallback(async () => {
    const container = exportContainerRef.current;
    if (!container) return;
    setExporting(true);
    setError(null);
    try {
      await exportDeckToPdf(deck, container);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export failed");
    } finally {
      setExporting(false);
    }
  }, [deck]);

  const layoutIcons = useMemo(
    () => LAYOUTS.map((l) => ({ ...l, Icon: LAYOUT_ICONS[l.id] })),
    [],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Slides</h2>
        <input
          value={deck.title}
          onChange={(e) => setDeck((d) => ({ ...d, title: e.target.value }))}
          aria-label="Deck title"
          className="w-52 truncate border-0 bg-transparent text-[13px] font-semibold text-shell-text outline-none placeholder:text-shell-text-tertiary"
        />
        <span className="truncate text-[12px] text-shell-text-tertiary">
          {docs.length} deck{docs.length === 1 ? "" : "s"} &middot;{" "}
          {activeId ? formatUpdated(updatedAt) : "New deck"}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={startPresenting}
            className="flex h-8 items-center gap-1.5 rounded-[9px] border border-shell-border px-3 text-[12px] font-semibold text-shell-text-secondary hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Play size={14} />
            Present
          </button>
          <button
            type="button"
            onClick={exportPdf}
            disabled={exporting}
            className="flex h-8 items-center gap-1.5 rounded-[9px] border border-shell-border px-3 text-[12px] font-semibold text-shell-text-secondary hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:opacity-60"
          >
            <Download size={14} />
            {exporting ? "Exporting..." : "Export PDF"}
          </button>
          <button
            type="button"
            onClick={newDoc}
            className="flex h-8 items-center gap-1.5 rounded-[9px] border border-shell-border px-3 text-[12px] font-semibold text-shell-text-secondary hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Plus size={14} />
            New
          </button>
          <button
            type="button"
            onClick={saveDoc}
            disabled={saving}
            className="flex h-8 items-center gap-1.5 rounded-[9px] bg-gradient-to-br from-accent to-accent/70 px-3.5 text-[12px] font-bold text-white disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Save size={14} />
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>

      {error && (
        <p className="border-b border-shell-border px-3.5 py-1.5 text-[12px] text-red-400" role="alert">
          {error}
        </p>
      )}

      <div className="flex min-h-0 flex-1">
        {/* slide thumbnail rail */}
        <aside
          className="flex w-[180px] flex-none flex-col gap-2 overflow-auto border-r border-shell-border bg-shell-bg-deep p-3"
          aria-label="Slide thumbnails"
        >
          {loading && <p className="px-1 py-1 text-[12px] text-shell-text-tertiary">Loading...</p>}
          {deck.slides.map((s, i) => (
            <div key={s.id} className="group relative">
              <button
                type="button"
                aria-label={`Slide ${i + 1}: ${s.title || "Untitled slide"}`}
                aria-current={s.id === selectedSlideId ? "true" : undefined}
                onClick={() => setSelectedSlideId(s.id)}
                className={`relative w-full overflow-hidden rounded-lg border text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                  s.id === selectedSlideId ? "outline outline-2 outline-offset-1" : ""
                }`}
                style={{
                  borderColor:
                    s.id === selectedSlideId ? "var(--color-accent, #8b92a3)" : "rgba(255,255,255,0.08)",
                  outlineColor: s.id === selectedSlideId ? "#a9b0c2" : undefined,
                }}
              >
                <SlideCanvas slide={s} className="pointer-events-none w-full" />
                <span
                  className="absolute left-1.5 top-1 rounded bg-black/40 px-1 text-[9px] text-white"
                  aria-hidden="true"
                >
                  {i + 1}
                </span>
              </button>
              <div className="absolute right-1 top-1 flex flex-col gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
                <button
                  type="button"
                  aria-label={`Move slide ${i + 1} up`}
                  disabled={i === 0}
                  onClick={() => handleMoveSlide(s.id, -1)}
                  className="grid h-5 w-5 place-items-center rounded bg-black/55 text-white hover:bg-black/75 disabled:pointer-events-none disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
                >
                  <ChevronUp size={12} />
                </button>
                <button
                  type="button"
                  aria-label={`Move slide ${i + 1} down`}
                  disabled={i === deck.slides.length - 1}
                  onClick={() => handleMoveSlide(s.id, 1)}
                  className="grid h-5 w-5 place-items-center rounded bg-black/55 text-white hover:bg-black/75 disabled:pointer-events-none disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
                >
                  <ChevronDown size={12} />
                </button>
                <button
                  type="button"
                  aria-label={`Delete slide ${i + 1}`}
                  disabled={deck.slides.length <= 1}
                  onClick={() => handleDeleteSlide(s.id)}
                  className="grid h-5 w-5 place-items-center rounded bg-black/55 text-white hover:bg-red-500/80 disabled:pointer-events-none disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            </div>
          ))}
          <button
            type="button"
            onClick={handleAddSlide}
            aria-label="Add slide"
            className="flex h-9 flex-none items-center justify-center gap-1.5 rounded-lg border border-dashed border-shell-border text-[12px] font-semibold text-shell-text-secondary hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Plus size={14} />
            Add slide
          </button>
        </aside>

        {/* live preview stage */}
        <div className="flex flex-1 flex-col items-center justify-center overflow-auto bg-shell-bg-deep p-6">
          <SlideCanvas slide={selectedSlide} className="w-full max-w-[640px] rounded-[10px] shadow-card" />
        </div>

        {/* right panel: layout + edit form */}
        <aside className="flex w-[280px] flex-none flex-col gap-4 overflow-auto border-l border-shell-border bg-shell-bg p-[18px]">
          <div>
            <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
              Layout
            </div>
            <div className="grid grid-cols-2 gap-2" role="group" aria-label="Slide layout">
              {layoutIcons.map(({ id, label, Icon }) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed={selectedSlide.layout === id}
                  onClick={() => patchSelected({ layout: id })}
                  className={`flex items-center gap-1.5 rounded-lg border px-2 py-2 text-[11px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                    selectedSlide.layout === id
                      ? "border-accent/60 bg-accent-soft text-shell-text"
                      : "border-shell-border bg-shell-surface text-shell-text-secondary hover:bg-shell-surface-active hover:text-shell-text"
                  }`}
                >
                  <Icon size={14} />
                  {label}
                </button>
              ))}
            </div>
          </div>

          <label className="flex flex-col gap-1 text-[11px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
            Title
            <input
              value={selectedSlide.title}
              onChange={(e) => patchSelected({ title: e.target.value })}
              aria-label="Slide title"
              className="rounded-lg border border-shell-border bg-shell-surface px-2.5 py-2 text-[12.5px] font-normal normal-case tracking-normal text-shell-text outline-none placeholder:text-shell-text-tertiary focus-visible:ring-2 focus-visible:ring-accent/40"
            />
          </label>

          {selectedSlide.layout !== "blank" && (
            <label className="flex flex-col gap-1 text-[11px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
              Body
              <textarea
                value={selectedSlide.body}
                onChange={(e) => patchSelected({ body: e.target.value })}
                aria-label="Slide body"
                rows={3}
                className="resize-none rounded-lg border border-shell-border bg-shell-surface px-2.5 py-2 text-[12.5px] font-normal normal-case tracking-normal text-shell-text outline-none placeholder:text-shell-text-tertiary focus-visible:ring-2 focus-visible:ring-accent/40"
              />
            </label>
          )}

          {(selectedSlide.layout === "title-content" || selectedSlide.layout === "two-column") && (
            <label className="flex flex-col gap-1 text-[11px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
              Bullets (one per line)
              <textarea
                value={selectedSlide.bullets.join("\n")}
                onChange={(e) => patchSelected({ bullets: e.target.value.split("\n") })}
                onBlur={(e) =>
                  patchSelected({
                    bullets: e.target.value.split("\n").filter((b) => b.trim() !== ""),
                  })
                }
                aria-label="Slide bullets, one per line"
                rows={4}
                className="resize-none rounded-lg border border-shell-border bg-shell-surface px-2.5 py-2 text-[12.5px] font-normal normal-case tracking-normal text-shell-text outline-none placeholder:text-shell-text-tertiary focus-visible:ring-2 focus-visible:ring-accent/40"
              />
            </label>
          )}

          <div className="flex flex-col gap-1.5">
            <span className="text-[11px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
              Image
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => imageInputRef.current?.click()}
                className="flex h-8 items-center gap-1.5 rounded-lg border border-shell-border bg-shell-surface px-2.5 text-[12px] font-semibold text-shell-text-secondary hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <ImagePlus size={14} />
                {selectedSlide.imageDataUri ? "Replace" : "Insert"}
              </button>
              {selectedSlide.imageDataUri && (
                <button
                  type="button"
                  aria-label="Remove image"
                  onClick={() => patchSelected({ imageDataUri: undefined })}
                  className="grid h-8 w-8 place-items-center rounded-lg border border-shell-border text-shell-text-secondary hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                >
                  <X size={14} />
                </button>
              )}
            </div>
            <input
              ref={imageInputRef}
              type="file"
              accept="image/*"
              aria-label="Insert slide image"
              className="sr-only"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file) handleImageFile(file);
              }}
            />
          </div>

          <label className="flex flex-col gap-1 text-[11px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
            Speaker notes
            <textarea
              value={selectedSlide.notes ?? ""}
              onChange={(e) => patchSelected({ notes: e.target.value })}
              aria-label="Speaker notes"
              rows={3}
              className="resize-none rounded-lg border border-shell-border bg-shell-surface px-2.5 py-2 text-[12.5px] font-normal normal-case tracking-normal text-shell-text outline-none placeholder:text-shell-text-tertiary focus-visible:ring-2 focus-visible:ring-accent/40"
            />
          </label>
        </aside>
      </div>

      {/* documents sidebar row (below body, matching Write/Calc's saved-docs list) */}
      <div className="flex h-[132px] flex-none border-t border-shell-border bg-shell-bg-deep">
        <div className="flex w-full flex-col overflow-hidden">
          <div className="border-b border-shell-border px-3 py-1.5 text-[11px] font-bold uppercase tracking-wide text-shell-text-tertiary">
            Saved decks
          </div>
          <div className="flex flex-1 items-center gap-2 overflow-x-auto p-2">
            {!loading && docs.length === 0 && (
              <p className="px-2 text-[12px] text-shell-text-tertiary">No saved decks yet</p>
            )}
            {docs.map((doc) => (
              <button
                key={doc.id}
                type="button"
                onClick={() => openDoc(doc.id)}
                className={`h-full w-[140px] flex-none rounded-lg px-2.5 py-2 text-left text-[12px] transition-colors hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                  activeId === doc.id ? "bg-shell-surface text-shell-text" : "text-shell-text-secondary"
                }`}
              >
                <div className="truncate font-semibold">{doc.title}</div>
                <div className="truncate text-[10px] text-shell-text-tertiary">
                  {formatUpdated(doc.updated_at)}
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* present mode overlay: always mounted (not display:none) so
          requestFullscreen can be called synchronously from the Present
          button's click handler; sized to 0 while inactive. */}
      <div
        ref={presentStageRef}
        aria-hidden={!isPresenting}
        className={isPresenting ? "fixed inset-0 z-[999]" : "fixed left-0 top-0 h-0 w-0 overflow-hidden"}
      >
        {isPresenting && (
          <PresentMode
            deck={deck}
            index={Math.min(presentIndex, deck.slides.length - 1)}
            onNext={() => setPresentIndex((i) => Math.min(i + 1, deck.slides.length - 1))}
            onPrev={() => setPresentIndex((i) => Math.max(i - 1, 0))}
            onExit={exitPresenting}
          />
        )}
      </div>

      {/* offscreen export capture surface: one fixed-size render per slide,
          always kept in sync with the deck so exportPdf can rasterize
          without a mount-timing race. */}
      <div
        ref={exportContainerRef}
        aria-hidden="true"
        style={{ position: "fixed", top: 0, left: -100000, pointerEvents: "none" }}
      >
        {deck.slides.map((s) => (
          <div
            key={s.id}
            data-export-slide
            style={{ width: EXPORT_PAGE_WIDTH, height: EXPORT_PAGE_HEIGHT }}
          >
            <SlideCanvas slide={s} fixedScale={EXPORT_PAGE_WIDTH / 640} />
          </div>
        ))}
      </div>
    </div>
  );
}
