import "./excalidraw-assets";
import { useMemo, useState, useEffect, useRef } from "react";
import type { ComponentProps } from "react";
import { Excalidraw, convertToExcalidrawElements, CaptureUpdateAction } from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";
import { CanvasElement, canvasApi } from "./canvas-api";
import { elementToSkeleton } from "./element-to-excalidraw";
import { mermaidToExcalidraw, type ExcalidrawElements } from "./mermaid-to-elements";
import { createSceneSync, type SyncSceneElement } from "./excalidraw-sync";
import { isPlaceholder } from "./tldraw-to-excalidraw";

// Excalidraw view over the canonical CanvasElement scene. Most kinds map
// synchronously; mermaid/flowchart kinds render their real diagram via
// mermaid-to-excalidraw, falling back to the placeholder rectangle while the
// (async) conversion runs or if it fails. Legacy user_shape rows carrying a
// tldraw_shape blob are converted at read time via tldraw-to-excalidraw.

const DIAGRAM_KINDS = new Set(["mermaid", "flowchart"]);

type ExcalidrawAPI = Parameters<
  NonNullable<ComponentProps<typeof Excalidraw>["excalidrawAPI"]>
>[0];

type SkeletonInput = Parameters<typeof convertToExcalidrawElements>[0];

// Branded types from Excalidraw (not exported from main module, use type assertions)
type FileId = string & { _brand: "FileId" };
type DataURL = string & { _brand: "DataURL" };
type AllowedMimeType = 
  | "image/svg+xml"
  | "image/png"
  | "image/jpeg"
  | "image/gif"
  | "image/webp"
  | "image/bmp"
  | "image/x-icon"
  | "image/avif"
  | "image/jfif"
  | "application/octet-stream";

export interface ExcalidrawBoardProps {
  elements: CanvasElement[];
  theme?: "light" | "dark";
  projectId?: string;
  projectSlug?: string;
  elementId?: string | null;
  getElement?: (id: string) => CanvasElement | undefined;
  readOnly?: boolean;
}

function live(elements: CanvasElement[]): CanvasElement[] {
  return elements
    .filter((el) => el.deleted_at == null)
    .slice()
    .sort((a, b) => (a.z_index || 0) - (b.z_index || 0));
}

function firstNonEmptyString(...vals: unknown[]): string {
  for (const v of vals) {
    if (typeof v === "string" && v.length > 0) return v;
  }
  return "";
}

function a11yLabelFor(el: CanvasElement): string {
  const p = (el.payload ?? {}) as Record<string, unknown>;
  const text = firstNonEmptyString(p.text, p.alt, p.title, p.label);
  if (text) return `${el.kind}, ${el.author_id}, ${text}`;
  return `${el.kind}, ${el.author_id}`;
}

export function ExcalidrawBoard({
  elements,
  theme = "light",
  projectId,
  projectSlug,
  elementId,
  getElement,
  readOnly,
}: ExcalidrawBoardProps) {
  const [api, setApi] = useState<ExcalidrawAPI | null>(null);
  // Converted diagram elements keyed by the CanvasElement id; absent until the
  // async mermaid conversion resolves.
  const [diagrams, setDiagrams] = useState<Record<string, ExcalidrawElements>>({});

  const getElementRef = useRef(getElement);
  getElementRef.current = getElement;
  const stableGetElement = useRef((id: string) => getElementRef.current?.(id)).current;

  // Keep the latest getElement in a ref so the sync can always read the current
  // element map without recreating when the function identity changes.

  // Re-run conversion only when a diagram element's id/source/position changes.
  const diagramKey = useMemo(
    () =>
      JSON.stringify(
        live(elements)
          .filter((el) => DIAGRAM_KINDS.has(el.kind))
          .map((el) => [el.id, el.x, el.y, (el.payload || {}).source]),
      ),
    [elements],
  );

  useEffect(() => {
    let cancelled = false;
    const diagramEls = live(elements).filter((el) => DIAGRAM_KINDS.has(el.kind));
    if (diagramEls.length === 0) {
      setDiagrams({});
      return;
    }
    (async () => {
      // Convert diagrams in parallel: each parse is independent, so the total
      // wait is the slowest single diagram, not the sum.
      const converted = await Promise.all(
        diagramEls.map(async (el) => {
          const src = String((el.payload || {}).source ?? "");
          return [el.id, await mermaidToExcalidraw(src, el.x, el.y)] as const;
        }),
      );
      if (!cancelled) setDiagrams(Object.fromEntries(converted));
    })();
    return () => {
      cancelled = true;
    };
    // diagramKey captures the inputs that affect conversion.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [diagramKey]);

  const sceneElements = useMemo(() => {
    const ordered = live(elements);
    // Walk the elements in z_index order (live() sorts them) so cross-kind
    // layering is preserved: a diagram is not forced above a regular element
    // that has a higher z_index. A ready diagram contributes its converted
    // elements; everything else (regular kinds, and a diagram still converting)
    // maps through the base skeleton.
    const readyDiagram = (el: CanvasElement): ExcalidrawElements | null => {
      const ready = DIAGRAM_KINDS.has(el.kind) ? diagrams[el.id] : undefined;
      return ready && ready.length > 0 ? ready : null;
    };
    // Every skeleton goes through ONE convertToExcalidrawElements call: an
    // arrow skeleton (mindmap_edge) binds start/end by the target's id, and the
    // converter only resolves a binding to an element in the same call, so a
    // per-element conversion leaves every edge unbound. Ready diagrams are
    // already converted and are spliced back in below.
    const skeletons = ordered
      .filter((el) => !readyDiagram(el))
      .map((el) => elementToSkeleton(el, { rows: elements }));
    const converted =
      skeletons.length > 0 ? convertToExcalidrawElements(skeletons as unknown as SkeletonInput) : [];
    // One skeleton can convert to several elements (a container plus its bound
    // label text), so group by the row id elementToSkeleton stamps into
    // customData; a label text carries no customData and follows its container.
    // Map every stamped element first so a label emitted before its container
    // still finds its row.
    const taosIdOf = (x: ExcalidrawElements[number]): string | undefined => {
      const taosId = (x.customData as { taos_id?: unknown } | undefined)?.taos_id;
      return typeof taosId === "string" ? taosId : undefined;
    };
    const rowOf = new Map<string, string>();
    for (const x of converted) {
      const taosId = taosIdOf(x);
      if (taosId) rowOf.set(x.id, taosId);
    }
    const byRow = new Map<string, ExcalidrawElements[number][]>();
    let lastRow: string | undefined;
    for (const x of converted) {
      const containerId = "containerId" in x ? x.containerId : null;
      const row = taosIdOf(x) ?? (containerId && rowOf.get(containerId)) ?? lastRow;
      if (!row) continue;
      lastRow = row;
      const group = byRow.get(row);
      if (group) group.push(x);
      else byRow.set(row, [x]);
    }
    return { elements: ordered.flatMap((el) => readyDiagram(el) ?? byRow.get(el.id) ?? []), byRow };
  }, [elements, diagrams]);

  const sceneElementsArray = sceneElements.elements;
  const byRow = sceneElements.byRow;

  // Sync layer: created once per projectId when interactive (not readOnly).
  // When active it receives every scene the board applies (markRemote) and
  // every change Excalidraw reports (onLocalChange via onChange), and
  // determines which writes to issue.
  const sync = useMemo(() => {
    if (!projectId || readOnly) return null;
    return createSceneSync({
      projectId,
      elementId: elementId ?? null,
      api: canvasApi,
      getElement: stableGetElement,
    });
  }, [projectId, elementId, readOnly, stableGetElement]);

  // Cleanup sync on unmount or when projectId/readOnly changes.
  useEffect(() => {
    if (!sync) return;
    return () => { const s = sync; void s.flush().catch(() => {}).finally(() => s.dispose()); };
  }, [sync]);

  const hasFit = useRef(false);
  // Excalidraw reads initialData once at mount; push later scenes (async
  // diagrams) through the imperative API. Fit the viewport to the content only
  // on the first non-empty scene -- refitting on every update would yank a
  // user's pan/zoom back once interactions land.
  useEffect(() => {
    if (!api) return;
    api.updateScene({ elements: sceneElementsArray, captureUpdate: CaptureUpdateAction.NEVER });
    if (sync) sync.markRemote(sceneElementsArray as unknown as readonly SyncSceneElement[]);
    if (!hasFit.current && sceneElementsArray.length > 0) {
      api.scrollToContent(sceneElementsArray, { fitToContent: true, animate: false });
      hasFit.current = true;
    }
  }, [api, sceneElementsArray, sync]);

  const inFlightFileIds = useRef<Set<string>>(new Set());
  const loadedFileIds = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!api || !projectSlug) return;
    const ordered = live(elements);
    const imageRows = ordered.filter((el) => el.kind === "image");
    for (const row of imageRows) {
      const fileId = String((row.payload || {}).file_id ?? "");
      if (!fileId || inFlightFileIds.current.has(fileId) || loadedFileIds.current.has(fileId)) continue;
      inFlightFileIds.current.add(fileId);
      (async () => {
        try {
          const res = await fetch(`/api/projects/${projectSlug}/files/canvas/${fileId}`);
          if (!res.ok) {
            inFlightFileIds.current.delete(fileId);
            return;
          }
          const blob = await res.blob();
          const mimeType = (blob.type || "image/png") as AllowedMimeType;
          const reader = new FileReader();
          reader.onload = () => {
            if (api && reader.result) {
              api.addFiles([{ id: fileId as FileId, dataURL: reader.result as DataURL, mimeType, created: Date.now() }]);
              inFlightFileIds.current.delete(fileId);
              loadedFileIds.current.add(fileId);
            }
          };
          reader.onerror = () => {
            inFlightFileIds.current.delete(fileId);
          };
          reader.readAsDataURL(blob);
        } catch {
          inFlightFileIds.current.delete(fileId);
        }
      })();
    }
  }, [api, projectSlug, elements]);

  const viewModeEnabled = !projectId || !!readOnly;

  const orderedLive = live(elements);
  const a11yItems = orderedLive.map((el) => {
    const readyDiagram = DIAGRAM_KINDS.has(el.kind) ? diagrams[el.id] : undefined;
    const convertedForRow = readyDiagram && readyDiagram.length > 0
      ? readyDiagram
      : byRow.get(el.id);
    const hasPlaceholder = convertedForRow?.some((x) => isPlaceholder(x as any)) ?? false;
    const label = hasPlaceholder
      ? `not convertible, element ${el.id}`
      : a11yLabelFor(el);
    return <li key={el.id}>{label}</li>;
  });

  return (
    <div style={{ position: "absolute", inset: 0 }} data-testid="excalidraw-board">
      <ul className="sr-only" aria-label="Canvas elements" role="list">
        {a11yItems}
      </ul>
      <Excalidraw
        excalidrawAPI={setApi}
        theme={theme}
        viewModeEnabled={viewModeEnabled}
        zenModeEnabled
        UIOptions={{ tools: { image: false } }}
        onChange={sync ? (els) => { void sync.onLocalChange(els as unknown as readonly SyncSceneElement[]); } : undefined}
        initialData={{
          elements: sceneElementsArray,
          appState: {
            viewBackgroundColor: theme === "dark" ? "#0b0f17" : "#ffffff",
          },
        }}
      />
    </div>
  );
}

export default ExcalidrawBoard;