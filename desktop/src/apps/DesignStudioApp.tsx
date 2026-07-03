import { useState, useEffect, useCallback, useMemo } from "react";
import { PenLine, LayoutGrid, Plus, Sparkles, Circle } from "lucide-react";
import { ModelBrowser } from "@/components/ModelBrowser";
import { DesignView } from "./designstudio/DesignView";
import { TemplatesView, type TemplateChoice } from "./designstudio/TemplatesView";
import { MagicView } from "./designstudio/MagicView";
import { createImageElement } from "./designstudio/elementFactory";
import {
  DEFAULT_ARTBOARD,
  type Artboard,
  type CanvasElement,
  type DesignStudioView,
  type GeneratedImage,
} from "./designstudio/types";
import type { ImageModel } from "./images/types";

const RAIL: { id: DesignStudioView; label: string; icon: typeof PenLine }[] = [
  { id: "design", label: "Design", icon: PenLine },
  { id: "templates", label: "Templates", icon: LayoutGrid },
  { id: "elements", label: "Elements", icon: Plus },
  { id: "magic", label: "Magic", icon: Sparkles },
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
      setView("design");
    },
    [artboard.width, artboard.height],
  );

  const handleSelectTemplate = useCallback(
    (template: TemplateChoice) => {
      // Picking a template resets the canvas; confirm first so an accidental
      // click doesn't wipe unsaved work. (Persistence is a later phase.)
      if (
        canvasElements.length > 0 &&
        !window.confirm("Start from this template? Your current design will be cleared.")
      ) {
        return;
      }
      setArtboard({ name: template.name, width: template.width, height: template.height });
      setCanvasElements([]);
      setView("design");
    },
    [canvasElements.length],
  );

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
              onElementsChange={setCanvasElements}
              artboard={artboard}
            />
          )}
          {view === "templates" && <TemplatesView onSelectTemplate={handleSelectTemplate} />}
          {view === "elements" && (
            <DesignView
              elements={canvasElements}
              onElementsChange={setCanvasElements}
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