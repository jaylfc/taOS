import { useState, useEffect, useCallback, useMemo } from "react";
import { PenLine, LayoutGrid, Plus, Sparkles, Circle, FolderOpen, Save, FilePlus2 } from "lucide-react";
import { ModelBrowser } from "@/components/ModelBrowser";
import { DesignView } from "./designstudio/DesignView";
import { TemplatesView, type TemplateChoice } from "./designstudio/TemplatesView";
import { MagicView } from "./designstudio/MagicView";
import { LibraryView } from "./designstudio/LibraryView";
import { createImageElement } from "./designstudio/elementFactory";
import { createDesign, getDesign, updateDesign, MAX_CONTENT_BYTES } from "./designstudio/designs-api";
import {
  DEFAULT_ARTBOARD,
  isValidDesignContent,
  type Artboard,
  type CanvasElement,
  type DesignContent,
  type DesignStudioView,
  type GeneratedImage,
} from "./designstudio/types";
import type { ImageModel } from "./images/types";

const RAIL: { id: DesignStudioView; label: string; icon: typeof PenLine }[] = [
  { id: "design", label: "Design", icon: PenLine },
  { id: "templates", label: "Templates", icon: LayoutGrid },
  { id: "elements", label: "Elements", icon: Plus },
  { id: "magic", label: "Magic", icon: Sparkles },
  { id: "library", label: "Library", icon: FolderOpen },
];

function randomSeed(): number {
  return Math.floor(Math.random() * 1_000_000);
}

export function DesignStudioApp({ windowId: _windowId }: { windowId: string }) {
  const [view, setView] = useState<DesignStudioView>("design");
  const [canvasElements, setCanvasElements] = useState<CanvasElement[]>([]);
  const [artboard, setArtboard] = useState<Artboard>(DEFAULT_ARTBOARD);

  const [magicPrompt, setMagicPrompt] = useState("");
  const [magicStyle, setMagicStyle] = useState<string | null>(null);
  const [magicResults, setMagicResults] = useState<GeneratedImage[]>([]);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorNeedsModel, setErrorNeedsModel] = useState(false);

  const [models, setModels] = useState<ImageModel[]>([]);
  const [selectedModelId, setSelectedModelId] = useState("");
  const [selectedVariantId, setSelectedVariantId] = useState("");
  const [browserOpen, setBrowserOpen] = useState(false);

  // Persistence: the currently-open saved design (if any), whether the
  // canvas has changes since the last save/open, and in-flight save/open
  // status. New designs start blank and unsaved.
  const [activeDesignId, setActiveDesignId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

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

  const needsModel = !selectedVariant?.downloaded;
  const canGenerate =
    !!magicPrompt.trim() && !generating && !!selectedVariant?.downloaded;

  const placeOnCanvas = useCallback(
    (img: GeneratedImage) => {
      setCanvasElements((prev) => [
        ...prev,
        createImageElement(prev, img.url, artboard.width, artboard.height, img.prompt),
      ]);
      setDirty(true);
      setView("design");
    },
    [artboard.width, artboard.height],
  );

  const handleSelectTemplate = useCallback(
    (template: TemplateChoice) => {
      // Picking a template resets the canvas; confirm first so an accidental
      // click doesn't wipe unsaved work.
      if (
        canvasElements.length > 0 &&
        !window.confirm("Start from this template? Your current design will be cleared.")
      ) {
        return;
      }
      setArtboard({ name: template.name, width: template.width, height: template.height });
      setCanvasElements([]);
      setActiveDesignId(null);
      setSaveError(null);
      setDirty(false);
      setView("design");
    },
    [canvasElements.length],
  );

  /** True if the user should be prompted before discarding in-memory edits. */
  const confirmDiscard = () =>
    !dirty || window.confirm("Discard unsaved changes to the current design?");

  const handleElementsChange = (next: CanvasElement[]) => {
    setCanvasElements(next);
    setDirty(true);
  };

  const newDesign = () => {
    if (!confirmDiscard()) return;
    setCanvasElements([]);
    setArtboard(DEFAULT_ARTBOARD);
    setActiveDesignId(null);
    setSaveError(null);
    setDirty(false);
  };

  const openDesign = async (id: string) => {
    if (!confirmDiscard()) return;
    setSaveError(null);
    try {
      const doc = await getDesign(id);
      let content: DesignContent;
      try {
        const parsed: unknown = JSON.parse(doc.content);
        if (!isValidDesignContent(parsed)) throw new Error("malformed design data");
        content = parsed;
      } catch {
        setSaveError("This design's saved data is corrupted; opened a blank design instead.");
        content = { artboard: DEFAULT_ARTBOARD, elements: [] };
      }
      setArtboard(content.artboard);
      setCanvasElements(content.elements);
      setActiveDesignId(doc.id);
      setDirty(false);
      setView("design");
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Open failed");
    }
  };

  const saveDesign = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const content = JSON.stringify({ artboard, elements: canvasElements });
      // Catch an over-cap design (usually too many/too-large inlined images)
      // here with a clear message rather than letting it fail only at PUT
      // time with a raw 413. The cap mirrors the backend's MAX_CONTENT_BYTES.
      if (new Blob([content]).size > MAX_CONTENT_BYTES) {
        throw new Error(
          "This design is too large to save (over 5 MB). Remove or shrink some images and try again.",
        );
      }
      const name = artboard.name.trim() || "Untitled design";
      const saved = activeDesignId
        ? await updateDesign(activeDesignId, name, content)
        : await createDesign(name, content);
      setActiveDesignId(saved.id);
      setDirty(false);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const handleRenamed = (id: string, name: string) => {
    if (activeDesignId === id) {
      setArtboard((prev) => ({ ...prev, name }));
    }
  };

  const runGenerate = useCallback(async () => {
    const usePrompt = magicPrompt.trim();
    if (!usePrompt) return;
    if (!selectedVariant?.downloaded) {
      setError("Install an image generation model first.");
      return;
    }

    setGenerating(true);
    setError(null);
    setErrorNeedsModel(false);

    const styledPrompt = magicStyle
      ? `${usePrompt}, ${magicStyle.toLowerCase()} style`
      : usePrompt;

    try {
      const res = await fetch("/api/images/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: styledPrompt,
          model: selectedModelId,
          variant: selectedVariantId,
          size: "512x512",
          steps: 4,
          seed: randomSeed(),
          guidance_scale: 7.5,
        }),
      });

      if (res.ok) {
        const ct = res.headers.get("content-type") ?? "";
        if (!ct.includes("application/json")) {
          setError("Generation returned an unexpected response format.");
        } else {
          try {
            const data = await res.json();
            if (data.filename || data.id) {
              const id = (data.filename as string) ?? (data.id as string);
              const url = (data.path as string) ?? `/data/workspace/images/generated/${id}`;
              const img: GeneratedImage = { id, url, prompt: styledPrompt };
              setMagicResults((prev) => [img, ...prev]);
              placeOnCanvas(img);
            } else if (data.error) {
              setError(String(data.error));
            } else {
              setError("Generation succeeded but returned no image data.");
            }
          } catch {
            setError("Generation returned invalid JSON.");
          }
        }
      } else {
        const data = await res.json().catch(() => ({}));
        setErrorNeedsModel(res.status === 502 || res.status === 503);
        setError(
          (data as { error?: string }).error ??
            `Generation failed (${res.status})`,
        );
      }
    } catch (e) {
      setError(`Generation error: ${(e as Error).message}`);
    }

    setGenerating(false);
  }, [
    magicPrompt,
    magicStyle,
    selectedVariant,
    selectedModelId,
    selectedVariantId,
    placeOnCanvas,
  ]);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-shell-bg text-shell-text select-none">
      {/* persistence bar -- name / dirty state / New / Save, shared across all views */}
      <div className="flex h-11 flex-none items-center gap-2.5 border-b border-shell-border bg-shell-bg-deep px-4">
        <input
          aria-label="Design name"
          value={artboard.name}
          onChange={(e) => {
            setArtboard((prev) => ({ ...prev, name: e.target.value }));
            setDirty(true);
          }}
          className="h-8 w-[220px] rounded-lg border border-shell-border bg-shell-surface px-3 text-[12.5px] font-semibold text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        />
        {dirty && <span className="text-[11px] text-shell-text-tertiary">Unsaved changes</span>}
        {saveError && (
          <span role="alert" className="truncate text-[11px] text-red-400">
            {saveError}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={newDesign}
            className="flex h-8 items-center gap-1.5 rounded-[9px] border border-shell-border px-3 text-[12px] font-semibold text-shell-text-secondary hover:bg-shell-surface-active focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <FilePlus2 size={14} /> New
          </button>
          <button
            type="button"
            onClick={() => void saveDesign()}
            disabled={saving}
            className="flex h-8 items-center gap-1.5 rounded-[9px] bg-gradient-to-br from-accent to-accent/70 px-3.5 text-[12px] font-bold text-white disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Save size={14} /> {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <nav
          aria-label="Design Studio views"
          className="flex w-[68px] flex-none flex-col items-center gap-1.5 border-r border-shell-border bg-shell-bg-deep py-3.5"
        >
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
            aria-label="Brand"
            className="flex h-[46px] w-[46px] flex-col items-center justify-center gap-0.5 rounded-xl text-[9px] font-semibold text-shell-text-tertiary transition-colors hover:bg-white/10 hover:text-shell-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Circle size={21} />
            Brand
          </button>
        </nav>

        <div className="flex min-w-0 flex-1 flex-col">
          {view === "design" && (
            <DesignView
              elements={canvasElements}
              onElementsChange={handleElementsChange}
              artboard={artboard}
            />
          )}
          {view === "templates" && <TemplatesView onSelectTemplate={handleSelectTemplate} />}
          {view === "elements" && (
            <DesignView
              elements={canvasElements}
              onElementsChange={handleElementsChange}
              artboard={artboard}
            />
          )}
          {view === "magic" && (
            <MagicView
              prompt={magicPrompt}
              onPromptChange={setMagicPrompt}
              style={magicStyle}
              onStyleChange={setMagicStyle}
              results={magicResults}
              generating={generating}
              canGenerate={canGenerate}
              error={error}
              errorNeedsModel={errorNeedsModel}
              needsModel={needsModel}
              onGenerate={() => void runGenerate()}
              onPickModel={() => setBrowserOpen(true)}
              onUseResult={placeOnCanvas}
            />
          )}
          {view === "library" && (
            <LibraryView onOpenDesign={(id) => void openDesign(id)} onRenamed={handleRenamed} />
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
          setError(null);
          setErrorNeedsModel(false);
        }}
      />
    </div>
  );
}