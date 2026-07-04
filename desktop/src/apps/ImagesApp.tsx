import { useState, useEffect, useCallback, useMemo } from "react";
import { Sparkles, LayoutGrid, Pencil, Settings2 } from "lucide-react";
import { ModelBrowser } from "@/components/ModelBrowser";
import { CreateView } from "./images/CreateView";
import { LibraryView } from "./images/LibraryView";
import { EditView } from "./images/EditView";
import {
  type GeneratedImage,
  type ImageModel,
  type GenerateParams,
  type StudioView,
  type GenerateMode,
  type LibraryFilter,
} from "./images/types";

/* ------------------------------------------------------------------ */
/*  Images Studio — shell                                              */
/*                                                                     */
/*  Left icon rail (Create / Library / Edit / Models) + the active     */
/*  surface. Shared backend wiring (list / generate / delete /          */
/*  download / models) lives here; the views are presentational.       */
/* ------------------------------------------------------------------ */

const RAIL: { id: StudioView; label: string; icon: typeof Sparkles }[] = [
  { id: "create", label: "Create", icon: Sparkles },
  { id: "library", label: "Library", icon: LayoutGrid },
  { id: "edit", label: "Edit", icon: Pencil },
];

function randomSeed(): number {
  return Math.floor(Math.random() * 1_000_000);
}

export function ImagesApp({ windowId: _windowId }: { windowId: string }) {
  const [view, setView] = useState<StudioView>("create");

  // gallery / results
  const [images, setImages] = useState<GeneratedImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeResultId, setActiveResultId] = useState<string | null>(null);
  const [librarySelectedId, setLibrarySelectedId] = useState<string | null>(
    null,
  );
  const [editImage, setEditImage] = useState<GeneratedImage | null>(null);

  // model catalog
  const [models, setModels] = useState<ImageModel[]>([]);
  const [selectedModelId, setSelectedModelId] = useState<string>("");
  const [selectedVariantId, setSelectedVariantId] = useState<string>("");
  const [browserOpen, setBrowserOpen] = useState(false);

  // create form
  const [mode, setMode] = useState<GenerateMode>("single");
  const [prompt, setPrompt] = useState("");
  const [size, setSize] = useState(512);
  const [steps, setSteps] = useState(4);
  const [guidance, setGuidance] = useState(7.5);
  const [style, setStyle] = useState<string | null>(null);
  const [seed, setSeed] = useState("");

  // library
  const [libFilter, setLibFilter] = useState<LibraryFilter>("all");

  /* ----------------------------- images -------------------------- */

  const fetchImages = useCallback(async () => {
    try {
      const res = await fetch("/api/images", {
        headers: { Accept: "application/json" },
      });
      if (res.ok) {
        const ct = res.headers.get("content-type") ?? "";
        if (ct.includes("application/json")) {
          const data = await res.json();
          const list = Array.isArray(data)
            ? data
            : Array.isArray(data?.images)
              ? data.images
              : [];
          const mapped: GeneratedImage[] = list.map(
            (raw: Record<string, unknown>) => ({
              id: (raw.filename as string) ?? (raw.id as string) ?? "",
              url: (raw.path as string) ?? (raw.url as string) ?? "",
              prompt: (raw.prompt as string) ?? "",
              model: (raw.model as string) ?? "",
              size: (raw.size as string | number) ?? "",
              steps: (raw.steps as number) ?? 0,
              seed: (raw.seed as number) ?? 0,
              guidance: (raw.guidance_scale as number) ?? 0,
              backend: (raw.backend as string) ?? undefined,
              createdAt:
                (raw.created_at as string) ?? new Date().toISOString(),
            }),
          );
          setImages(mapped);
          setLoading(false);
          return;
        }
      }
    } catch {
      /* fall through */
    }
    setImages([]);
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchImages();
  }, [fetchImages]);

  /* --------------------------- model catalog --------------------- */

  const refreshModels = useCallback(async () => {
    try {
      const res = await fetch("/api/models", {
        headers: { Accept: "application/json" },
      });
      if (!res.ok) return [] as ImageModel[];
      const data = await res.json();
      if (!data || !Array.isArray(data.models)) return [] as ImageModel[];
      const imageModels: ImageModel[] = data.models.filter(
        (m: ImageModel) =>
          Array.isArray(m.capabilities) &&
          m.capabilities.includes("image-generation"),
      );
      setModels(imageModels);
      return imageModels;
    } catch {
      return [] as ImageModel[];
    }
  }, []);

  useEffect(() => {
    (async () => {
      const imageModels = await refreshModels();
      for (const m of imageModels) {
        const dl = m.variants?.find((v) => v.downloaded);
        if (dl) {
          setSelectedModelId(m.id);
          setSelectedVariantId(dl.id);
          return;
        }
      }
    })();
  }, [refreshModels]);

  const selectedModel = useMemo(
    () => models.find((m) => m.id === selectedModelId),
    [models, selectedModelId],
  );
  const selectedVariant = useMemo(
    () => selectedModel?.variants.find((v) => v.id === selectedVariantId),
    [selectedModel, selectedVariantId],
  );

  const modelName = selectedModel?.name ?? "No model";
  const modelMeta = selectedVariant
    ? `${selectedVariant.name}${
        selectedVariant.backend?.length
          ? ` · ${selectedVariant.backend.join("/")}`
          : ""
      }`
    : "Pick a model";

  const canGenerate =
    !!prompt.trim() &&
    !generating &&
    !!selectedVariant &&
    !!selectedVariant.downloaded;

  /* ----------------------------- generate ------------------------ */

  const runGenerate = useCallback(
    async (
      overrideSeed?: number,
      overridePrompt?: string,
      overrides?: { size?: number; steps?: number; guidance?: number },
    ) => {
      const usePrompt = (overridePrompt ?? prompt).trim();
      if (!usePrompt) return;
      if (!selectedVariant || !selectedVariant.downloaded) {
        setError("Select a downloaded model first.");
        return;
      }
      setGenerating(true);
      setError(null);

      const effectiveSeed =
        overrideSeed ?? (seed ? parseInt(seed, 10) : randomSeed());
      const styledPrompt = style
        ? `${usePrompt}, ${style.toLowerCase()} style`
        : usePrompt;

      const params: GenerateParams = {
        prompt: styledPrompt,
        model: selectedModelId,
        variant: selectedVariantId,
        size: `${overrides?.size ?? size}x${overrides?.size ?? size}`,
        steps: overrides?.steps ?? steps,
        seed: effectiveSeed,
        guidance_scale: (overrides?.guidance ?? guidance) || 7.5,
      };

      try {
        const res = await fetch("/api/images/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(params),
        });
        if (res.ok) {
          const ct = res.headers.get("content-type") ?? "";
          if (ct.includes("application/json")) {
            const data = await res.json();
            if (data.filename || data.id) {
              const newId = (data.filename as string) ?? (data.id as string);
              await fetchImages();
              setActiveResultId(newId);
            } else if (data.error) {
              setError(String(data.error));
            }
          }
        } else {
          const data = await res.json().catch(() => ({}));
          setError(
            (data as { error?: string }).error ??
              `Generation failed (${res.status})`,
          );
        }
      } catch (e) {
        setError(`Generation error: ${(e as Error).message}`);
      }
      setGenerating(false);
    },
    [
      prompt,
      seed,
      style,
      selectedVariant,
      selectedModelId,
      selectedVariantId,
      size,
      steps,
      guidance,
      fetchImages,
    ],
  );

  const handleReroll = useCallback(() => {
    setSeed(String(randomSeed()));
  }, []);

  /* ----------------------------- delete -------------------------- */

  const handleDelete = useCallback((id: string) => {
    setImages((prev) => prev.filter((img) => img.id !== id));
    setLibrarySelectedId((cur) => (cur === id ? null : cur));
    setActiveResultId((cur) => (cur === id ? null : cur));
    fetch(`/api/images/${encodeURIComponent(id)}`, { method: "DELETE" }).catch(
      () => {},
    );
  }, []);

  const handleDownload = useCallback((img: GeneratedImage) => {
    if (!img.url) return;
    const a = document.createElement("a");
    a.href = img.url;
    a.download = `${img.prompt.slice(0, 30).replace(/\s+/g, "-") || "image"}-${
      img.seed
    }.png`;
    a.click();
  }, []);

  /* --------- re-roll from library/create: reuse params + new seed --- */

  const rerollFrom = useCallback(
    (img: GeneratedImage) => {
      if (typeof img.size === "number") setSize(img.size);
      if (img.steps) setSteps(img.steps);
      if (img.guidance) setGuidance(img.guidance);
      setPrompt(img.prompt);
      setView("create");
      // Pass params explicitly: the setState calls above update the controls
      // for next time, but won't be visible to runGenerate this tick.
      void runGenerate(randomSeed(), img.prompt, {
        size: typeof img.size === "number" ? img.size : undefined,
        steps: img.steps || undefined,
        guidance: img.guidance || undefined,
      });
    },
    [runGenerate],
  );

  const openInEdit = useCallback((img: GeneratedImage) => {
    setEditImage(img);
    setView("edit");
  }, []);

  /* --------- Apply adjust (real client-side op via canvas) -------- */

  const applyAdjust = useCallback(
    (filterCss: string) => {
      if (!editImage?.url) return;
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => {
        const canvas = document.createElement("canvas");
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        ctx.filter = filterCss;
        ctx.drawImage(img, 0, 0);
        canvas.toBlob((blob) => {
          if (!blob) return;
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `${
            editImage.prompt.slice(0, 30).replace(/\s+/g, "-") || "image"
          }-adjusted.png`;
          a.click();
          URL.revokeObjectURL(url);
        }, "image/png");
      };
      img.src = editImage.url;
    },
    [editImage],
  );

  /* ------------------------------ render ------------------------- */

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-shell-bg text-shell-text select-none">
      <div className="flex min-h-0 flex-1">
        {/* left rail */}
        <div className="flex w-[68px] flex-none flex-col items-center gap-1.5 border-r border-shell-border bg-shell-bg-deep py-3.5">
          {RAIL.map((r) => {
            const Icon = r.icon;
            const on = view === r.id;
            return (
              <button
                key={r.id}
                type="button"
                aria-label={r.label}
                aria-current={on ? "page" : undefined}
                onClick={() => setView(r.id)}
                className={`flex h-[46px] w-[46px] flex-col items-center justify-center gap-0.5 rounded-xl text-[9px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
                  on
                    ? "bg-gradient-to-b from-accent/25 to-transparent text-accent"
                    : "text-shell-text-tertiary hover:bg-white/10 hover:text-shell-text-secondary"
                }`}
              >
                <Icon size={21} />
                {r.label}
              </button>
            );
          })}
          <div className="flex-1" />
          <button
            type="button"
            aria-label="Models"
            onClick={() => setBrowserOpen(true)}
            className="flex h-[46px] w-[46px] flex-col items-center justify-center gap-0.5 rounded-xl text-[9px] font-semibold text-shell-text-tertiary transition-colors hover:bg-white/10 hover:text-shell-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Settings2 size={21} />
            Models
          </button>
        </div>

        {/* active surface */}
        <div className="flex min-w-0 flex-1 flex-col">
          {view === "create" && (
            <CreateView
              mode={mode}
              onModeChange={setMode}
              modelName={modelName}
              modelMeta={modelMeta}
              onPickModel={() => setBrowserOpen(true)}
              prompt={prompt}
              onPromptChange={setPrompt}
              size={size}
              onSizeChange={setSize}
              steps={steps}
              onStepsChange={setSteps}
              guidance={guidance}
              onGuidanceChange={setGuidance}
              style={style}
              onStyleChange={setStyle}
              seed={seed}
              onReroll={handleReroll}
              results={images}
              activeResultId={activeResultId}
              onSelectResult={setActiveResultId}
              generating={generating}
              canGenerate={canGenerate}
              onGenerate={() => void runGenerate()}
              error={error}
              onEditResult={openInEdit}
              onDownloadResult={handleDownload}
            />
          )}

          {view === "library" && (
            <LibraryView
              images={images}
              loading={loading}
              filter={libFilter}
              onFilterChange={setLibFilter}
              selectedId={librarySelectedId}
              onSelect={setLibrarySelectedId}
              onReroll={rerollFrom}
              onDownload={handleDownload}
              onDelete={handleDelete}
              onEdit={openInEdit}
            />
          )}

          {view === "edit" && (
            <EditView
              image={editImage}
              onApplyAdjust={applyAdjust}
              onEdited={async ({ url, image_ref, backend }) => {
                // Refresh the library and re-select the new result for
                // further editing. `backend` is the tier that actually ran
                // (may differ from what was requested — see EditView's
                // downgrade notice), so it replaces the stale value here too.
                await fetchImages();
                setEditImage((cur) =>
                  cur ? { ...cur, id: image_ref, url, backend } : cur,
                );
                setLibrarySelectedId(image_ref);
              }}
            />
          )}
        </div>
      </div>

      <ModelBrowser
        open={browserOpen}
        onClose={() => setBrowserOpen(false)}
        capability="image-generation"
        onModelDownloaded={async (modelId, variantId) => {
          await refreshModels();
          setSelectedModelId(modelId);
          setSelectedVariantId(variantId);
        }}
      />
    </div>
  );
}
