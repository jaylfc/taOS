import "./excalidraw-assets";
import {
  useMemo,
  useState,
  useEffect,
  useRef,
  useCallback,
} from "react";
import type { ComponentProps } from "react";
import { Excalidraw, convertToExcalidrawElements, type ExcalidrawElement } from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";
import { CanvasElement } from "./canvas-api";
import {
  elementToSkeleton,
  elementsToSkeletons,
} from "./element-to-excalidraw";
import { mermaidToExcalidraw, type ExcalidrawElements } from "./mermaid-to-elements";
import { createCanvasStore } from "./canvas-store";
import { subscribeCanvasStream, type CanvasEvent } from "./canvas-sse";
import { canvasApi } from "./canvas-api";
import { createSceneSync, type SceneSync, type SyncSceneElement } from "./excalidraw-sync";
import { useIsMobile } from "../../../hooks/use-is-mobile";
import { useThemeStore } from "@/stores/theme-store";
import { isPlaceholder } from "./tldraw-to-excalidraw";

type ExcalidrawAPI = Parameters<
  NonNullable<ComponentProps<typeof Excalidraw>["excalidrawAPI"]>
>[0];

const DIAGRAM_KINDS = new Set(["mermaid", "flowchart"]);

export interface ExcalidrawBoardProps {
  projectId: string;
  projectSlug: string;
  elementId?: string | null;
}

const EMPTY: never[] = [];

export function ExcalidrawBoard({
  projectId,
  projectSlug,
  elementId,
}: ExcalidrawBoardProps) {
  const isMobile = useIsMobile();
  const theme = useThemeStore((s) => s.scheme);
  const store = useMemo(() => createCanvasStore(), []);
const [boardState, setBoardState] = useState<{
  elements: CanvasElement[];
  scopeElements: CanvasElement[];
  visibleElements: CanvasElement[];
}>({ elements: [], scopeElements: [], visibleElements: [] });
  const [api, setApi] = useState<ExcalidrawAPI | null>(null);
  const [diagrams, setDiagrams] = useState<Record<string, ExcalidrawElements>>({});
  const [files, setFiles] = useState<readonly { id: string; data: string }[]>([]);
  const syncRef = useRef<SceneSync | null>(null);

  // Drive scope/visible state from the store so every SSE-driven upsert or
  // remove re-evaluates the element_id scope, the soft-delete filter, and the
  // whole scene below.
  useEffect(() => {
    const handler = () => {
      const arr = Object.values(store.getState().elements);
      const scoped = elementId == null ? arr : arr.filter((e) => e.element_id === elementId);
      const live = scoped.filter((e) => e.deleted_at == null);
      setBoardState({ elements: arr, scopeElements: scoped, visibleElements: live });
    };
    handler();
    const unsub = store.subscribe(handler);
    return unsub;
  }, [store, elementId]);

  // Initial load + SSE subscription (unfiltered upserts; the subscriber above
  // scopes them). SSE events are applied to the store and re-flow through the
  // scene, and the sync core later drops echoes via version stamps.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const els = await canvasApi.listElements(projectId, elementId);
      if (!cancelled) {
        store.getState().seed(els);
        setBoardState((prev) => ({ ...prev, elements: els }));
      }
    })();
    const unsub = subscribeCanvasStream(projectId, store);
    return () => { cancelled = true; unsub(); };
  }, [projectId, elementId, store]);

  // Sync core: turns Excalidraw changes into REST PATCH/POST/DELETE calls and
  // tells the board which remote rows are echoes of its own writes.
  useEffect(() => {
    syncRef.current = createSceneSync({
      projectId,
      elementId,
      api: canvasApi,
      getElement: (id) => boardState.scopeElements.find((e) => e.id === id),
      onConflict: (rowId) => console.warn("canvas: conflict on", rowId),
      onError: (err, rowId) => console.error("canvas: write error on", rowId, err),
    });
    return () => { syncRef.current?.dispose(); };
  }, [projectId, elementId, boardState]);

  // Convert every non-diagram skeleton in a single batch so cross-kind bindings
  // (mindmap_edge from/to) resolve. Ready mermaid/flowchart diagrams are spliced
  // in here with the row's groupIds, locked, and updated_at stamp. Soft-deleted
  // rows are excluded from the scene.
  const sceneElements = useMemo(() => {
    const visible = boardState.scopeElements.filter((el) => el.deleted_at == null);
    const nonDiagram = visible.filter((el) => !DIAGRAM_KINDS.has(el.kind));
    const diagram = visible.filter((el) => DIAGRAM_KINDS.has(el.kind));
    let converted = convertToExcalidrawElements(
      nonDiagram.map((el) => elementToSkeleton(el)) as any,
    );

    for (const el of diagram) {
      const ready = diagrams[el.id];
      if (!ready) continue;
      for (const part of ready) {
        const s = part as any;
        s.groupIds = [el.id] as any;
        s.locked = true;
        (s.customData ??= {}) as SyncSceneElement["customData"];
        s.customData.taos_id = el.id;
        s.customData.taos_kind = el.kind;
        s.customData.taos_updated_at = el.updated_at;
        converted = [...converted, ...ready];
      }
    }

    const byId = new Map<string, unknown>();
    for (const el of converted) { byId.set((el as any).id, el); }

    for (const el of converted) {
      const s = el as any;
      const row = boardState.scopeElements.find((e) => e.id === s.id);
      if (row) {
        (s.customData ??= {}) as SyncSceneElement["customData"];
        s.customData.taos_id = row.id;
        s.customData.taos_kind = row.kind;
        s.customData.taos_updated_at = row.updated_at;
      }
      if (s.label) {
        // Inline label (SkeletonLabel on a rectangle): stamp the bound text
        // child so the sync maps it to the container row, and register it in
        // boundElements in case the converter did not do so.
        (s.label as any).customData ??= {};
        (s.label as any).customData.taos_id = s.id;
        (s.label as any).customData.taos_kind = s.customData?.taos_kind;
        (s.label as any).customData.taos_updated_at = s.customData?.taos_updated_at;
        (s.label as any).customData.containerId = s.id;
        (s.boundElements ??= []).push({ id: s.id, type: "text" });
      }
      if (s.boundElements) {
        for (const b of s.boundElements) {
          const child = byId.get(b.id);
          if (child) {
            (child as any).customData ??= {};
            (child as any).customData.taos_id = s.id;
            (child as any).customData.containerId = s.id;
          }
        }
      }
    }
    return converted;
  }, [boardState, diagrams]);

  // Ready mermaid diagrams, keyed by element id. Converted in parallel so the
  // total wait is the slowest single diagram, not the sum.
  useEffect(() => {
    let cancelled = false;
    const diagramEls = boardState.scopeElements.filter(
      (el) => DIAGRAM_KINDS.has(el.kind) && el.deleted_at == null,
    );
    if (diagramEls.length === 0) {
      setDiagrams({});
      return;
    }
    (async () => {
      const converted = await Promise.all(
        diagramEls.map(async (el) => [
          el.id,
          await mermaidToExcalidraw(
            String((el.payload ?? {}).source ?? ""),
            el.x,
            el.y,
          ),
        ] as const),
      );
      if (!cancelled) setDiagrams(Object.fromEntries(converted));
    })();
    return () => { cancelled = true; };
  }, [boardState]);

  // Each canvas image file is fetched as a dataURL once and registered with the
  // backend so it is persisted in files/canvas beside the element rows. The
  // returned entries populate Excalidraw's file dictionary.
  useEffect(() => {
    let cancelled = false;
    const fileIds = new Set<string>();
    for (const el of boardState.scopeElements) {
      if (el.kind === "image") {
        const fid = (el.payload ?? {}).file_id;
        if (fid) fileIds.add(fid as string);
      }
    }
    (async () => {
      const entries = await Promise.all(
        Array.from(fileIds).map(async (fileId) => {
          const res = await fetch(`/api/projects/${projectSlug}/files/canvas/${fileId}`);
          if (!res.ok) {
            console.warn("canvas: image not found", fileId);
            return { id: fileId, data: "" } as const;
          }
          const blob = await res.blob();
          const data = await new Promise<string>((resolve, reject) => {
            const fr = new FileReader();
            fr.onload = () => resolve(fr.result as string);
            fr.onerror = reject;
            fr.readAsDataURL(blob);
          });
          try {
            await canvasApi.addFiles(projectSlug, [{ file_id: fileId, data }]);
          } catch (err) {
            console.warn("canvas: addFiles failed", err);
          }
          return { id: fileId, data };
        }),
      );
      if (!cancelled) setFiles(entries);
    })();
    return () => { cancelled = true; };
  }, [boardState, projectSlug]);

  // Push the current scene to Excalidraw and tell the sync core it is remote,
  // so later local edits only write when they are newer. Runs on every scene
  // change and whenever the excalidrawAPI is ready. markRemote must precede
  // updateScene so the echoed onChange resolves by version.
  useEffect(() => {
    if (!api) return;
    syncRef.current?.markRemote(sceneElements as any);
    api.updateScene({ elements: sceneElements });
  }, [sceneElements, api]);

  const onChange = useCallback(
    (els: readonly ExcalidrawElement[]) => {
      if (els.length === 0) return;
      syncRef.current?.onLocalChange(els as any);
    },
    [],
  );

  const themeValue = theme === "dark" ? "dark" : "light";

  return (
    <div style={{ position: "absolute", inset: 0 }} data-testid="excalidraw-board">
      <Excalidraw
        excalidrawAPI={setApi}
        theme={themeValue}
        viewModeEnabled={isMobile}
        UIOptions={{
          tools: { image: false },
        }}
        initialData={{
          elements: sceneElements,
          appState: {
            viewBackgroundColor: theme === "dark" ? "#0b0f17" : "#ffffff",
          },
          files,
        }}
        onChange={onChange}
      />
      <ul
        aria-label="Canvas elements"
        style={{
          position: "absolute",
          left: "-9999px",
          width: "1px",
          height: "1px",
          overflow: "hidden",
          margin: 0,
          padding: 0,
          listStyle: "none",
        }}
      >
        {boardState.visibleElements.map((el) => {
          const s = elementToSkeleton(el);
          const isPh = isPlaceholder(s);
          let label = "";
          if (isPh) {
            label = `not convertible, element ${s.customData?.taos_original_element_id ?? el.id}`;
          } else if (el.kind === "note" || el.kind === "text") {
            label = String((el.payload ?? {}).text ?? "");
          } else if (el.kind === "link") {
            const p = (el.payload ?? {}) as any;
            label = String(p.title || p.url || "");
          } else if (el.kind === "image") {
            const p = (el.payload ?? {}) as any;
            label = String(p.alt || p.file_id || "");
          }
          return (
            <li key={el.id}>
              {el.kind}, {el.author_kind} {el.author_id}: {label}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default ExcalidrawBoard;
