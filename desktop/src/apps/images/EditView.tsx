import { useState, useMemo, useRef, useEffect, useCallback } from "react";
import {
  SlidersHorizontal,
  Eraser,
  Sparkles,
  Scissors,
  Maximize2,
  Expand,
  Shuffle,
  Undo2,
  Check,
  ImageIcon,
  Loader2,
  Store,
  AlertTriangle,
} from "lucide-react";
import { Slider, Chip, GroupLabel, Segmented } from "./controls";
import {
  STAGED_TOOLS,
  TOOL_CAPABILITY,
  type EditCapabilities,
  type EditTool,
  type GeneratedImage,
} from "./types";

/* ------------------------------------------------------------------ */
/*  EditView — tool rail + canvas + tool options + action bar          */
/*                                                                     */
/*  Adjust is a REAL client-side op (CSS filters). Erase / Inpaint /    */
/*  Remove BG / Extend / Upscale are wired to the editing backend       */
/*  (IOPaint) via /api/images/edit, /remove-bg, /upscale. Ops whose     */
/*  capability has no healthy backend are disabled with an "install a   */
/*  backend" affordance. Variations stays staged (no dedicated backend).*/
/* ------------------------------------------------------------------ */

interface ToolDef {
  id: EditTool;
  label: string;
  icon: typeof SlidersHorizontal;
  title: string;
  desc: string;
}

const TOOLS: ToolDef[] = [
  {
    id: "adjust",
    label: "Adjust",
    icon: SlidersHorizontal,
    title: "Adjust",
    desc: "Tune brightness, contrast and saturation. Applied live, on-device.",
  },
  {
    id: "erase",
    label: "Erase",
    icon: Eraser,
    title: "Magic Eraser",
    desc: "Brush over anything you want gone. taOS fills the area to match the surroundings.",
  },
  {
    id: "inpaint",
    label: "Inpaint",
    icon: Sparkles,
    title: "Inpaint",
    desc: "Paint a mask, then describe what should appear there instead.",
  },
  {
    id: "removebg",
    label: "Remove BG",
    icon: Scissors,
    title: "Remove background",
    desc: "Cut the subject out and drop a transparent background, on-device.",
  },
  {
    id: "extend",
    label: "Extend",
    icon: Expand,
    title: "Extend / outpaint",
    desc: "Grow the canvas and let taOS paint in the new edges.",
  },
  {
    id: "upscale",
    label: "Upscale",
    icon: Maximize2,
    title: "Upscale",
    desc: "Increase resolution and sharpen detail without artefacts.",
  },
  {
    id: "vary",
    label: "Vary",
    icon: Shuffle,
    title: "Variations",
    desc: "Generate fresh takes that keep the spirit of this image.",
  },
];

export interface EditViewProps {
  image: GeneratedImage | null;
  onApplyAdjust: (filterCss: string) => void;
  /** Called after a backend edit succeeds, with the new image's url + ref so
   *  the host can refresh the library and re-select the result.
   *  ``degraded``/``backend`` surface a silent tier downgrade (a Quality
   *  request served by the fast iopaint eraser, which ignores the prompt). */
  onEdited?: (result: {
    url: string;
    image_ref: string;
    degraded?: boolean;
    backend?: string;
  }) => void;
}

const DEFAULT_ADJUST = { brightness: 100, contrast: 100, saturation: 100 };

type Tier = "fast" | "quality";

export function EditView({ image, onApplyAdjust, onEdited }: EditViewProps) {
  const [tool, setTool] = useState<EditTool>("adjust");
  const [adjust, setAdjust] = useState(DEFAULT_ADJUST);
  const [brush, setBrush] = useState(54);
  const [replacePrompt, setReplacePrompt] = useState("");
  const [eraseMode, setEraseMode] = useState<"erase" | "replace">("erase");
  const [tier, setTier] = useState<Tier>("fast");
  const [scale, setScale] = useState<2 | 4>(2);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Set when the last edit result was served by a lower tier than requested
  // (e.g. Quality asked for flux-fill but iopaint's prompt-ignoring eraser
  // ran instead), so the result is never presented as if it were Quality.
  const [downgradeNotice, setDowngradeNotice] = useState<string | null>(null);

  const [caps, setCaps] = useState<EditCapabilities | null>(null);

  // mask-painting canvas (natural image pixels)
  const imgRef = useRef<HTMLImageElement | null>(null);
  const maskRef = useRef<HTMLCanvasElement | null>(null);
  const paintingRef = useRef(false);
  const [hasMask, setHasMask] = useState(false);

  /* ---- capabilities gate ---- */
  useEffect(() => {
    let cancelled = false;
    fetch("/api/images/edit/capabilities", {
      headers: { Accept: "application/json" },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: EditCapabilities | null) => {
        if (!cancelled && data) setCaps(data);
      })
      .catch(() => {
        /* leave caps null → ops gated off */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filterCss = useMemo(
    () =>
      `brightness(${adjust.brightness}%) contrast(${adjust.contrast}%) saturate(${adjust.saturation}%)`,
    [adjust],
  );

  const def = TOOLS.find((t) => t.id === tool) ?? TOOLS[0]!;
  const staged = STAGED_TOOLS.has(tool);

  // Is the current tool's capability backed by a healthy backend?
  const requiredCap = TOOL_CAPABILITY[tool];
  const capAvailable = !requiredCap || (caps ? caps[requiredCap] : false);
  const isMasking = tool === "erase" || tool === "inpaint";

  // Is the Quality tier's own backend (flux-fill) actually healthy? If not,
  // offering "Quality" would silently downgrade to the fast iopaint eraser,
  // which ignores the prompt — so it must be disabled, not just offered.
  const qualityHealthy = caps?.image_editing_tiers
    ? caps.image_editing_tiers.quality
    : false;

  // If Quality becomes unavailable (or capabilities load in as unavailable)
  // while it's selected, fall back to Fast rather than leave a disabled tier
  // selected that Apply would still submit.
  useEffect(() => {
    if (tier === "quality" && caps && !qualityHealthy) setTier("fast");
  }, [caps, qualityHealthy, tier]);

  const dirty =
    tool === "adjust" &&
    (adjust.brightness !== 100 ||
      adjust.contrast !== 100 ||
      adjust.saturation !== 100);

  /* ---- mask canvas sizing: match natural image, reset on image/tool ---- */
  const resetMask = useCallback(() => {
    const c = maskRef.current;
    if (c) {
      const ctx = c.getContext("2d");
      ctx?.clearRect(0, 0, c.width, c.height);
    }
    setHasMask(false);
  }, []);

  useEffect(() => {
    resetMask();
  }, [image?.url, tool, resetMask]);

  // Clear the downgrade notice on a tool switch (a stale notice from a
  // different op would be confusing). NOT keyed on image?.url: a successful
  // edit updates the displayed image right away, and the notice must survive
  // that swap so the user actually sees it.
  useEffect(() => {
    setDowngradeNotice(null);
  }, [tool]);

  function syncMaskSize() {
    const img = imgRef.current;
    const c = maskRef.current;
    if (!img || !c) return;
    if (c.width !== img.naturalWidth || c.height !== img.naturalHeight) {
      c.width = img.naturalWidth || img.width;
      c.height = img.naturalHeight || img.height;
    }
  }

  function paintAt(e: React.PointerEvent<HTMLCanvasElement>) {
    const c = maskRef.current;
    const img = imgRef.current;
    if (!c || !img) return;
    syncMaskSize();
    const rect = c.getBoundingClientRect();
    // Map display coords → natural pixel coords.
    const x = ((e.clientX - rect.left) / rect.width) * c.width;
    const y = ((e.clientY - rect.top) / rect.height) * c.height;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = "#ffffff";
    ctx.beginPath();
    // Scale brush from display px to natural px.
    const r = (brush / 2) * (c.width / rect.width);
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
    setHasMask(true);
  }

  function onPointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!isMasking) return;
    paintingRef.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
    paintAt(e);
  }
  function onPointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!paintingRef.current) return;
    paintAt(e);
  }
  function onPointerUp() {
    paintingRef.current = false;
  }

  /** Export the painted mask as a base64 PNG (no data-URI prefix). */
  function exportMask(): string | null {
    const c = maskRef.current;
    if (!c) return null;
    const url = c.toDataURL("image/png");
    return url.split(",")[1] ?? null;
  }

  function reset() {
    if (tool === "adjust") setAdjust(DEFAULT_ADJUST);
    setReplacePrompt("");
    setError(null);
    setDowngradeNotice(null);
    resetMask();
  }

  async function callEdit(
    path: string,
    payload: Record<string, unknown>,
  ): Promise<void> {
    setBusy(true);
    setError(null);
    setDowngradeNotice(null);
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(
          (data as { error?: string }).error ?? `Edit failed (${res.status})`,
        );
        return;
      }
      const { url, image_ref, degraded, backend } = data as {
        url?: string;
        image_ref?: string;
        degraded?: boolean;
        backend?: string;
      };
      if (url && image_ref) {
        // A quality request silently served by the fast iopaint eraser must
        // never read as a Quality result — surface it instead of dropping it.
        setDowngradeNotice(
          degraded
            ? "Served by the fast eraser — the Quality model isn't available, so the prompt was ignored."
            : null,
        );
        onEdited?.({ url, image_ref, degraded, backend });
        resetMask();
      }
    } catch {
      setError("Could not reach the editing backend.");
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!image) return;
    if (tool === "adjust") {
      onApplyAdjust(filterCss);
      return;
    }
    const ref = image.id;
    if (tool === "erase" || tool === "inpaint") {
      const mask = exportMask();
      if (!mask) {
        setError("Brush over a region first.");
        return;
      }
      await callEdit("/api/images/edit", {
        image_ref: ref,
        op: "inpaint",
        mask,
        prompt: tool === "inpaint" || eraseMode === "replace" ? replacePrompt : "",
        tier,
      });
    } else if (tool === "extend") {
      // Outpaint: IOPaint grows the canvas via enable_extender; an empty
      // mask is fine — the backend masks the new border itself.
      const c = document.createElement("canvas");
      c.width = 8;
      c.height = 8;
      const mask = c.toDataURL("image/png").split(",")[1] ?? "";
      await callEdit("/api/images/edit", {
        image_ref: ref,
        op: "outpaint",
        mask,
        prompt: replacePrompt,
        tier,
      });
    } else if (tool === "removebg") {
      await callEdit("/api/images/remove-bg", { image_ref: ref });
    } else if (tool === "upscale") {
      await callEdit("/api/images/upscale", { image_ref: ref, scale });
    }
  }

  const showTier = tool === "inpaint" || tool === "extend";
  const applyDisabled =
    !image ||
    busy ||
    staged ||
    !capAvailable ||
    (tool === "adjust" && !dirty) ||
    (isMasking && !hasMask);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* view header */}
      <div className="flex h-[54px] flex-none items-center gap-3 border-b border-shell-border px-[22px]">
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">Edit</h2>
        <span className="text-[12px] text-shell-text-tertiary truncate">
          {image
            ? `${image.prompt.split(" ").slice(0, 4).join(" ")} · ${
                typeof image.size === "number"
                  ? `${image.size} × ${image.size}`
                  : image.size
              }`
            : "No image selected"}
        </span>
      </div>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        {/* tool rail — horizontal scroller on mobile, vertical rail on desktop */}
        <div
          className="flex w-full md:w-[78px] flex-none flex-row md:flex-col items-center gap-1 overflow-x-auto md:overflow-x-visible border-b md:border-b-0 md:border-r border-shell-border bg-shell-bg-deep px-2 py-2 md:px-0 md:py-3"
          role="tablist"
          aria-label="Edit tools"
        >
          {TOOLS.map((t) => {
            const Icon = t.icon;
            const on = t.id === tool;
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={on}
                aria-label={t.label}
                onClick={() => setTool(t.id)}
                className={`flex w-[62px] flex-col items-center gap-1 rounded-xl py-2.5 text-[9.5px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                  on
                    ? "bg-gradient-to-b from-accent/25 to-transparent text-accent"
                    : "text-shell-text-tertiary hover:bg-white/10 hover:text-shell-text-secondary"
                }`}
              >
                <Icon size={20} />
                {t.label}
              </button>
            );
          })}
        </div>

        {/* canvas */}
        <div className="relative flex min-w-0 flex-1 items-center justify-center p-[22px]">
          {image && image.url ? (
            <div className="relative overflow-hidden rounded-2xl border border-shell-border shadow-[var(--shadow-window)]">
              <img
                ref={imgRef}
                src={image.url}
                alt={image.prompt}
                onLoad={syncMaskSize}
                style={tool === "adjust" ? { filter: filterCss } : undefined}
                className="block max-h-[430px] w-auto select-none"
                draggable={false}
              />
              {/* brush-mask painting canvas for erase / inpaint */}
              <canvas
                ref={maskRef}
                aria-label="Mask painting surface"
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerLeave={onPointerUp}
                className={`absolute inset-0 h-full w-full opacity-50 mix-blend-screen ${
                  isMasking ? "cursor-crosshair" : "pointer-events-none hidden"
                }`}
                style={{ touchAction: "none" }}
              />
              {busy && (
                <div className="absolute inset-0 flex items-center justify-center bg-shell-bg-deep/60 backdrop-blur-sm">
                  <Loader2 size={28} className="animate-spin text-accent" />
                </div>
              )}
              {downgradeNotice && (
                <div
                  role="status"
                  className="absolute inset-x-0 top-0 flex items-start gap-1.5 border-b border-amber-500/20 bg-amber-500/10 px-3 py-2 text-[11.5px] font-medium leading-snug text-amber-400/90 backdrop-blur-sm"
                >
                  <AlertTriangle
                    size={13}
                    className="mt-0.5 shrink-0 text-amber-400"
                    aria-hidden="true"
                  />
                  {downgradeNotice}
                </div>
              )}
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2 text-shell-text-tertiary">
              <ImageIcon size={40} className="opacity-30" />
              <p className="text-sm">Pick an image from the Library to edit</p>
            </div>
          )}
        </div>

        {/* options panel */}
        <div className="flex w-full md:w-[286px] flex-none flex-col gap-[18px] overflow-auto border-t md:border-t-0 md:border-l border-shell-border p-[18px]">
          <div className="flex items-center gap-2.5 text-[15px] font-bold tracking-[-0.01em]">
            <def.icon size={18} className="text-accent" />
            {def.title}
            {staged && (
              <span className="ml-auto rounded-full border border-shell-border bg-shell-surface px-2 py-0.5 text-[9.5px] font-semibold uppercase tracking-wide text-shell-text-tertiary">
                Soon
              </span>
            )}
          </div>
          <p className="-mt-2 text-[12px] leading-relaxed text-shell-text-secondary">
            {def.desc}
          </p>

          {tool === "adjust" && (
            <>
              <Slider
                id="edit-brightness"
                label="Brightness"
                value={adjust.brightness}
                min={0}
                max={200}
                display={`${adjust.brightness}%`}
                onChange={(v) =>
                  setAdjust((a) => ({ ...a, brightness: Math.round(v) }))
                }
              />
              <Slider
                id="edit-contrast"
                label="Contrast"
                value={adjust.contrast}
                min={0}
                max={200}
                display={`${adjust.contrast}%`}
                onChange={(v) =>
                  setAdjust((a) => ({ ...a, contrast: Math.round(v) }))
                }
              />
              <Slider
                id="edit-saturation"
                label="Saturation"
                value={adjust.saturation}
                min={0}
                max={200}
                display={`${adjust.saturation}%`}
                onChange={(v) =>
                  setAdjust((a) => ({ ...a, saturation: Math.round(v) }))
                }
              />
            </>
          )}

          {isMasking && (
            <>
              <Slider
                id="edit-brush"
                label="Brush size"
                value={brush}
                min={10}
                max={120}
                display={String(brush)}
                onChange={(v) => setBrush(Math.round(v))}
              />
              <div>
                <GroupLabel>
                  {tool === "inpaint" ? "Replace with" : "Or replace with"}
                </GroupLabel>
                <input
                  type="text"
                  value={replacePrompt}
                  onChange={(e) => setReplacePrompt(e.target.value)}
                  placeholder="optional prompt"
                  aria-label="Replacement prompt"
                  className="w-full rounded-xl border border-shell-border bg-shell-surface px-3 py-2.5 text-[12.5px] text-shell-text placeholder:text-shell-text-tertiary focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20"
                />
              </div>
              {tool === "erase" && (
                <div>
                  <GroupLabel>Mode</GroupLabel>
                  <div className="flex gap-[7px]">
                    <Chip
                      label="Erase"
                      on={eraseMode === "erase"}
                      onClick={() => setEraseMode("erase")}
                    />
                    <Chip
                      label="Replace"
                      on={eraseMode === "replace"}
                      onClick={() => setEraseMode("replace")}
                    />
                  </div>
                </div>
              )}
            </>
          )}

          {tool === "extend" && (
            <div>
              <GroupLabel>Fill prompt</GroupLabel>
              <input
                type="text"
                value={replacePrompt}
                onChange={(e) => setReplacePrompt(e.target.value)}
                placeholder="optional — describe the new edges"
                aria-label="Outpaint prompt"
                className="w-full rounded-xl border border-shell-border bg-shell-surface px-3 py-2.5 text-[12.5px] text-shell-text placeholder:text-shell-text-tertiary focus-visible:outline-none focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/20"
              />
            </div>
          )}

          {tool === "upscale" && (
            <div>
              <GroupLabel>Scale</GroupLabel>
              <Segmented<string>
                ariaLabel="Upscale factor"
                value={String(scale)}
                onChange={(v) => setScale(v === "4" ? 4 : 2)}
                options={[
                  { value: "2", label: "2×" },
                  { value: "4", label: "4×" },
                ]}
              />
            </div>
          )}

          {showTier && (
            <div>
              <GroupLabel>Quality tier</GroupLabel>
              <Segmented<Tier>
                ariaLabel="Quality tier"
                value={tier}
                onChange={setTier}
                options={[
                  { value: "fast", label: "Fast" },
                  {
                    value: "quality",
                    label: "Quality",
                    disabled: !qualityHealthy,
                    title: qualityHealthy
                      ? undefined
                      : "Quality model not installed",
                  },
                ]}
              />
            </div>
          )}

          {/* capability gate affordance */}
          {!staged && requiredCap && !capAvailable && (
            <div className="flex flex-col gap-2 rounded-xl border border-shell-border bg-shell-surface px-3 py-3 text-[12px] leading-relaxed text-shell-text-tertiary">
              <span className="flex items-center gap-1.5 font-semibold text-shell-text-secondary">
                <Store size={14} />
                Editing backend needed
              </span>
              Install an editing backend (IOPaint) from the Store to use this
              tool.
            </div>
          )}

          {staged && (
            <p className="rounded-xl border border-shell-border bg-shell-surface px-3 py-2.5 text-[12px] leading-relaxed text-shell-text-tertiary">
              This tool is coming soon.
            </p>
          )}

          {error && (
            <p
              role="alert"
              className="rounded-xl border border-shell-border bg-shell-surface px-3 py-2.5 text-[12px] leading-relaxed text-shell-text-secondary"
            >
              {error}
            </p>
          )}
        </div>
      </div>

      {/* action bar */}
      <div className="flex flex-none items-center gap-2.5 border-t border-shell-border bg-shell-bg-deep px-[22px] py-3">
        <button
          type="button"
          onClick={reset}
          disabled={busy}
          aria-label="Undo changes"
          className="flex h-[42px] items-center gap-1.5 rounded-xl border border-transparent px-4 text-[12.5px] font-semibold text-shell-text-tertiary transition-colors hover:bg-white/10 disabled:pointer-events-none disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          <Undo2 size={15} />
          Undo
        </button>
        <button
          type="button"
          onClick={reset}
          disabled={busy}
          aria-label="Reset"
          className="flex h-[42px] items-center rounded-xl border border-transparent px-4 text-[12.5px] font-semibold text-shell-text-tertiary transition-colors hover:bg-white/10 disabled:pointer-events-none disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          Reset
        </button>
        <button
          type="button"
          onClick={apply}
          disabled={applyDisabled}
          aria-label={
            staged
              ? "Apply (coming soon)"
              : !capAvailable
                ? "Apply (install a backend first)"
                : "Apply changes"
          }
          title={staged ? "Coming soon" : undefined}
          className="ml-auto flex h-[42px] items-center gap-1.5 rounded-xl border border-transparent bg-gradient-to-br from-accent to-accent/70 px-5 text-[12.5px] font-semibold text-white shadow-lg shadow-accent/20 transition-all hover:-translate-y-0.5 hover:brightness-105 disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          {busy ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Check size={16} />
          )}
          {staged ? "Coming soon" : busy ? "Working…" : "Apply"}
        </button>
      </div>
    </div>
  );
}
