import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Stage, Layer, Rect, Transformer } from "react-konva";
import type Konva from "konva";
import type { KonvaEventObject } from "konva/lib/Node";
import {
  Type,
  Square,
  Circle,
  Image as ImageIcon,
  Minus,
  Star,
  Undo,
  Redo2,
  Download,
  Plus as PlusIcon,
  Minus as MinusIcon,
  Maximize,
} from "lucide-react";
import type { Artboard, CanvasElement } from "./types";
import { ZOOM_STEPS } from "./types";
import {
  createImageElement,
  createLineElement,
  createShapeElement,
  createTextElement,
  duplicateElement,
} from "./elementFactory";
import { useElementHistory } from "./useElementHistory";
import { CanvasNode } from "./CanvasNode";
import { LayersPanel } from "./LayersPanel";
import { PropertiesPanel } from "./PropertiesPanel";

const ELEMENT_TILES: { label: string; icon: typeof Type; enabled: boolean }[] = [
  { label: "Text", icon: Type, enabled: true },
  { label: "Shape", icon: Square, enabled: true },
  { label: "Circle", icon: Circle, enabled: true },
  { label: "Image", icon: ImageIcon, enabled: true },
  { label: "Line", icon: Minus, enabled: true },
  { label: "Star", icon: Star, enabled: false },
];

const BRAND_SWATCHES = ["#6b7689", "#2c3142", "#cdd3df", "#3d8f7a", "#c98b5b", "#ffffff"];

function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n));
}

export interface DesignViewProps {
  elements: CanvasElement[];
  onElementsChange: (next: CanvasElement[]) => void;
  artboard: Artboard;
}

export function DesignView({ elements, onElementsChange, artboard }: DesignViewProps) {
  const { commit, undo, redo, canUndo, canRedo } = useElementHistory(elements, onElementsChange);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [stagePos, setStagePos] = useState({ x: 0, y: 0 });
  // Non-zero fallback so the stage has something to render before the first
  // ResizeObserver measurement lands (and in test environments that never
  // lay out a real container).
  const [stageSize, setStageSize] = useState({ width: 960, height: 640 });
  const [isSpaceDown, setIsSpaceDown] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingValue, setEditingValue] = useState("");

  const containerRef = useRef<HTMLDivElement | null>(null);
  const stageRef = useRef<Konva.Stage | null>(null);
  const transformerRef = useRef<Konva.Transformer | null>(null);
  const nodeMap = useRef(new Map<string, Konva.Node>());
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const selected = useMemo(
    () => elements.find((el) => el.id === selectedId) ?? null,
    [elements, selectedId],
  );

  // Drop the selection if the selected element was removed/undone away.
  useEffect(() => {
    if (selectedId && !elements.some((el) => el.id === selectedId)) {
      setSelectedId(null);
    }
  }, [elements, selectedId]);

  const fitToScreen = useCallback(
    (size = stageSize) => {
      if (size.width <= 0 || size.height <= 0) return;
      const next = clamp(
        Math.min(size.width / artboard.width, size.height / artboard.height) * 0.9,
        0.05,
        4,
      );
      setZoom(next);
      setStagePos({
        x: (size.width - artboard.width * next) / 2,
        y: (size.height - artboard.height * next) / 2,
      });
    },
    [artboard.width, artboard.height, stageSize],
  );

  // Track the available viewport for the Konva stage.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => {
      const width = el.clientWidth;
      const height = el.clientHeight;
      // Ignore zero-size measurements (e.g. before layout, or in jsdom which
      // never lays out real dimensions) so the stage keeps its fallback size.
      if (width > 0 && height > 0) setStageSize({ width, height });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Re-fit whenever the artboard size changes or the viewport becomes available.
  useEffect(() => {
    fitToScreen();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artboard.width, artboard.height, stageSize.width, stageSize.height]);

  // Attach the Transformer to the selected node.
  useEffect(() => {
    const transformer = transformerRef.current;
    if (!transformer) return;
    const node = selectedId ? nodeMap.current.get(selectedId) : null;
    if (node) {
      transformer.nodes([node]);
      transformer.enabledAnchors(
        selected?.type === "line"
          ? ["middle-left", "middle-right"]
          : [
              "top-left",
              "top-center",
              "top-right",
              "middle-right",
              "middle-left",
              "bottom-left",
              "bottom-center",
              "bottom-right",
            ],
      );
    } else {
      transformer.nodes([]);
    }
    transformer.getLayer()?.batchDraw();
  }, [selectedId, elements, selected?.type]);

  const registerNode = useCallback((id: string, node: Konva.Node | null) => {
    if (node) nodeMap.current.set(id, node);
    else nodeMap.current.delete(id);
  }, []);

  /* ------------------------------ mutations ----------------------------- */

  const addElement = useCallback(
    (kind: "text" | "rect" | "ellipse" | "line") => {
      const el =
        kind === "text"
          ? createTextElement(elements, artboard.width, artboard.height)
          : kind === "line"
            ? createLineElement(elements, artboard.width, artboard.height)
            : createShapeElement(elements, kind, artboard.width, artboard.height);
      commit([...elements, el]);
      setSelectedId(el.id);
    },
    [elements, artboard.width, artboard.height, commit],
  );

  const addImageFromFile = useCallback(
    (file: File) => {
      const reader = new FileReader();
      reader.onload = () => {
        const src = String(reader.result);
        const el = createImageElement(elements, src, artboard.width, artboard.height);
        commit([...elements, el]);
        setSelectedId(el.id);
      };
      reader.readAsDataURL(file);
    },
    [elements, artboard.width, artboard.height, commit],
  );

  const updateSelected = useCallback(
    (patch: Partial<CanvasElement>) => {
      if (!selectedId) return;
      commit(
        elements.map((el) => (el.id === selectedId ? ({ ...el, ...patch } as CanvasElement) : el)),
      );
    },
    [elements, selectedId, commit],
  );

  const patchElement = useCallback(
    (id: string, patch: Partial<CanvasElement>) => {
      commit(elements.map((el) => (el.id === id ? ({ ...el, ...patch } as CanvasElement) : el)));
    },
    [elements, commit],
  );

  const deleteElement = useCallback(
    (id: string) => {
      commit(elements.filter((el) => el.id !== id));
      setSelectedId((cur) => (cur === id ? null : cur));
    },
    [elements, commit],
  );

  const duplicateSelected = useCallback(() => {
    if (!selected) return;
    const copy = duplicateElement(selected, elements);
    commit([...elements, copy]);
    setSelectedId(copy.id);
  }, [selected, elements, commit]);

  const toggleVisibility = useCallback(
    (id: string) => {
      const target = elements.find((el) => el.id === id);
      commit(elements.map((el) => (el.id === id ? { ...el, visible: !el.visible } : el)));
      setSelectedId((cur) => (cur === id && target?.visible ? null : cur));
    },
    [elements, commit],
  );

  const reorder = useCallback(
    (id: string, direction: "up" | "down") => {
      const sorted = [...elements].sort((a, b) => a.zIndex - b.zIndex);
      const idx = sorted.findIndex((el) => el.id === id);
      const swapIdx = direction === "up" ? idx + 1 : idx - 1;
      if (idx < 0 || swapIdx < 0 || swapIdx >= sorted.length) return;
      const a = sorted[idx]!;
      const b = sorted[swapIdx]!;
      const zA = a.zIndex;
      const zB = b.zIndex;
      commit(
        elements.map((el) => {
          if (el.id === a.id) return { ...el, zIndex: zB } as CanvasElement;
          if (el.id === b.id) return { ...el, zIndex: zA } as CanvasElement;
          return el;
        }),
      );
    },
    [elements, commit],
  );

  /* --------------------------------- export ------------------------------ */

  const exportPng = useCallback(() => {
    const stage = stageRef.current;
    if (!stage || elements.length === 0) return;
    const contentLayer = stage.findOne<Konva.Layer>("#design-content-layer");
    if (!contentLayer) return;

    // The selection glow lives on the content layer (the Transformer handles
    // are on their own layer and are already excluded), so hide it on the
    // real Konva node for the instant of capture rather than round-tripping
    // through React state.
    const selectedNode = selectedId
      ? (nodeMap.current.get(selectedId) as Konva.Shape | undefined)
      : undefined;
    const hadShadow = selectedNode?.shadowEnabled();
    if (selectedNode && hadShadow) selectedNode.shadowEnabled(false);

    // The layer's canvas is only ever drawn at the stage's current pan/zoom,
    // so cropping it while panned/zoomed can miss content that is off-screen
    // or scaled. Reset the stage to an untransformed 1:1 view for the
    // instant of capture, grab the artboard at its true size, then restore
    // whatever view the user had.
    const savedScale = { x: stage.scaleX(), y: stage.scaleY() };
    const savedPos = stage.position();
    stage.scale({ x: 1, y: 1 });
    stage.position({ x: 0, y: 0 });
    stage.batchDraw();

    const dataUrl = contentLayer.toDataURL({
      x: 0,
      y: 0,
      width: artboard.width,
      height: artboard.height,
      pixelRatio: 2,
    });

    stage.scale(savedScale);
    stage.position(savedPos);
    stage.batchDraw();

    if (selectedNode && hadShadow) selectedNode.shadowEnabled(true);

    const a = document.createElement("a");
    a.href = dataUrl;
    a.download = `${artboard.name.trim().replace(/\s+/g, "-") || "design"}.png`;
    a.click();
  }, [elements.length, selectedId, artboard.width, artboard.height, artboard.name]);

  /* ------------------------------- zoom / pan ----------------------------- */

  const setZoomCentered = useCallback(
    (next: number) => {
      const clamped = clamp(next, 0.05, 4);
      setZoom(clamped);
      setStagePos({
        x: (stageSize.width - artboard.width * clamped) / 2,
        y: (stageSize.height - artboard.height * clamped) / 2,
      });
    },
    [stageSize, artboard.width, artboard.height],
  );

  const handleWheel = useCallback(
    (e: KonvaEventObject<WheelEvent>) => {
      e.evt.preventDefault();
      const stage = stageRef.current;
      if (!stage) return;
      const pointer = stage.getPointerPosition();
      if (!pointer) return;
      const oldScale = zoom;
      const mousePointTo = {
        x: (pointer.x - stagePos.x) / oldScale,
        y: (pointer.y - stagePos.y) / oldScale,
      };
      const direction = e.evt.deltaY > 0 ? -1 : 1;
      const newScale = clamp(direction > 0 ? oldScale * 1.08 : oldScale / 1.08, 0.05, 4);
      setZoom(newScale);
      setStagePos({
        x: pointer.x - mousePointTo.x * newScale,
        y: pointer.y - mousePointTo.y * newScale,
      });
    },
    [zoom, stagePos],
  );

  const handleStageMouseDown = useCallback(
    (e: KonvaEventObject<MouseEvent>) => {
      const evt = e.evt;
      if (evt.button === 1) {
        evt.preventDefault();
        const startX = evt.clientX;
        const startY = evt.clientY;
        const origin = { ...stagePos };
        const onMove = (ev: MouseEvent) => {
          setStagePos({ x: origin.x + (ev.clientX - startX), y: origin.y + (ev.clientY - startY) });
        };
        const onUp = () => {
          window.removeEventListener("mousemove", onMove);
          window.removeEventListener("mouseup", onUp);
        };
        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
        return;
      }
      const targetName = e.target.name?.();
      if (e.target === stageRef.current || targetName === "artboard-background") {
        setSelectedId(null);
      }
    },
    [stagePos],
  );

  /* ---------------------------- inline text edit -------------------------- */

  const beginEditText = useCallback(
    (id: string) => {
      const el = elements.find((e2) => e2.id === id);
      if (!el || el.type !== "text") return;
      setSelectedId(id);
      setEditingId(id);
      setEditingValue(el.text);
    },
    [elements],
  );

  const commitTextEdit = useCallback(() => {
    if (!editingId) return;
    const id = editingId;
    setEditingId(null);
    commit(
      elements.map((el) => (el.id === id && el.type === "text" ? { ...el, text: editingValue } : el)),
    );
  }, [editingId, editingValue, elements, commit]);

  const editingNode = editingId ? (nodeMap.current.get(editingId) as Konva.Text | undefined) : undefined;
  const editingEl = editingId ? elements.find((el) => el.id === editingId) : undefined;
  const editorStyle = useMemo(() => {
    if (!editingNode || !editingEl || editingEl.type !== "text" || !stageRef.current) return null;
    const stageBox = stageRef.current.container().getBoundingClientRect();
    const pos = editingNode.getAbsolutePosition();
    const scale = editingNode.getAbsoluteScale().x;
    return {
      position: "fixed" as const,
      top: stageBox.top + pos.y,
      left: stageBox.left + pos.x,
      width: editingEl.width * scale,
      height: editingEl.height * scale,
      fontSize: editingEl.fontSize * scale,
      fontFamily: editingEl.fontFamily,
      color: editingEl.fill,
      transform: `rotate(${editingEl.rotation}deg)`,
      transformOrigin: "top left",
    };
    // zoom + stagePos are read via the node's absolute position/scale, so they
    // must be in the dep list for the overlay to re-anchor if the user
    // zooms/pans mid-edit.
  }, [editingNode, editingEl, zoom, stagePos]);

  /* -------------------------------- keyboard ------------------------------- */

  useEffect(() => {
    function isTypingTarget(target: EventTarget | null): boolean {
      const el = target as HTMLElement | null;
      // Any focusable form control counts: a focused <select> (the zoom
      // picker) or color/number input must not have Delete/Backspace hijacked
      // into deleting the canvas selection.
      return (
        !!el &&
        (el.tagName === "INPUT" ||
          el.tagName === "TEXTAREA" ||
          el.tagName === "SELECT" ||
          el.isContentEditable)
      );
    }

    function onKeyDown(e: KeyboardEvent) {
      if (e.code === "Space" && !isTypingTarget(e.target)) {
        setIsSpaceDown(true);
      }
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "z" && !isTypingTarget(e.target)) {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
        return;
      }
      if (mod && e.key.toLowerCase() === "d" && !isTypingTarget(e.target)) {
        e.preventDefault();
        duplicateSelected();
        return;
      }
      if ((e.key === "Delete" || e.key === "Backspace") && selectedId && !isTypingTarget(e.target)) {
        e.preventDefault();
        deleteElement(selectedId);
      }
    }
    function onKeyUp(e: KeyboardEvent) {
      if (e.code === "Space") setIsSpaceDown(false);
    }
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, [undo, redo, duplicateSelected, deleteElement, selectedId]);

  const zoomPct = Math.round(zoom * 100);

  return (
    <>
      {/* view header */}
      <div
        className="flex flex-none items-center gap-3 border-b border-shell-border px-[22px]"
        style={{ height: "54px" }}
      >
        <h2 className="text-[17px] font-bold tracking-[-0.02em]">{artboard.name}</h2>
        <span className="text-[12px] text-shell-text-tertiary">
          {artboard.width} x {artboard.height}
        </span>
        <div className="ml-auto flex gap-1.5">
          <button
            type="button"
            aria-label="Undo"
            disabled={!canUndo}
            onClick={undo}
            className="flex h-[30px] items-center gap-1.5 rounded-[9px] border border-shell-border bg-shell-surface px-3 text-[11.5px] font-semibold text-shell-text-secondary transition-colors hover:text-shell-text disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Undo size={14} />
            Undo
          </button>
          <button
            type="button"
            aria-label="Redo"
            disabled={!canRedo}
            onClick={redo}
            className="flex h-[30px] items-center gap-1.5 rounded-[9px] border border-shell-border bg-shell-surface px-3 text-[11.5px] font-semibold text-shell-text-secondary transition-colors hover:text-shell-text disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            <Redo2 size={14} />
            Redo
          </button>
          <button
            type="button"
            aria-label="Export as PNG"
            disabled={elements.length === 0}
            onClick={exportPng}
            className="flex h-[30px] items-center gap-1.5 rounded-[9px] px-3 text-[11.5px] font-semibold text-white transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
            style={{ background: "linear-gradient(135deg,#a9b0c2,#8b92a3)", border: "none" }}
          >
            <Download size={14} />
            Export
          </button>
        </div>
      </div>

      {/* three-column body (stacks on small widths) */}
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        {/* left elements + layers panel */}
        <div className="flex w-full flex-none flex-col overflow-auto border-b border-shell-border bg-shell-bg-deep px-3 py-3.5 md:w-[210px] md:border-b-0 md:border-r">
          <div className="mb-2 ml-0.5 text-[10.5px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
            Add
          </div>
          <div className="mb-4 grid grid-cols-2 gap-2 md:grid-cols-2">
            {ELEMENT_TILES.map(({ label, icon: Icon, enabled }) => (
              <button
                key={label}
                type="button"
                aria-label={label}
                disabled={!enabled}
                title={enabled ? label : "Coming soon"}
                onClick={() => {
                  if (label === "Text") addElement("text");
                  else if (label === "Shape") addElement("rect");
                  else if (label === "Circle") addElement("ellipse");
                  else if (label === "Line") addElement("line");
                  else if (label === "Image") fileInputRef.current?.click();
                }}
                className="flex aspect-square cursor-pointer flex-col items-center justify-center rounded-[11px] border border-shell-border bg-shell-surface text-shell-text-secondary transition-all hover:-translate-y-0.5 hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:translate-y-0"
              >
                <Icon size={22} />
              </button>
            ))}
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            aria-hidden="true"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) addImageFromFile(file);
              e.target.value = "";
            }}
          />

          <div className="mb-2 ml-0.5 text-[10.5px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
            Layers
          </div>
          <div className="mb-4">
            <LayersPanel
              elements={elements}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onToggleVisibility={toggleVisibility}
              onReorder={reorder}
              onDelete={deleteElement}
            />
          </div>

          <div className="mb-2 ml-0.5 text-[10.5px] font-bold uppercase tracking-[0.06em] text-shell-text-tertiary">
            Brand colors
          </div>
          <div className="flex flex-wrap gap-[7px]">
            {BRAND_SWATCHES.map((hex) => (
              <button
                key={hex}
                type="button"
                aria-label={`Apply ${hex}`}
                disabled={!selected || selected.type === "line" || selected.type === "image"}
                onClick={() => updateSelected({ fill: hex } as Partial<CanvasElement>)}
                className="h-6 w-6 cursor-pointer rounded-[7px] transition-transform hover:scale-110 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                style={{ background: hex, border: "1px solid rgba(255,255,255,0.12)" }}
              />
            ))}
          </div>
        </div>

        {/* center canvas stage */}
        <div
          className="flex min-w-0 flex-1 flex-col"
          style={{
            background:
              "repeating-conic-gradient(rgba(255,255,255,0.018) 0% 25%, transparent 0% 50%) 0 0/22px 22px, var(--color-shell-bg, #1d1d1f)",
          }}
        >
          {/* stage bar */}
          <div className="flex h-10 flex-none items-center gap-2.5 border-b border-shell-border bg-shell-bg-deep px-[18px]">
            <span className="text-[11px] font-semibold text-shell-text-secondary">
              {selected ? selected.type[0]!.toUpperCase() + selected.type.slice(1) : "No selection"}
            </span>
            <div className="ml-auto flex items-center gap-1">
              <button
                type="button"
                aria-label="Zoom out"
                onClick={() => setZoomCentered(zoom / 1.25)}
                className="rounded-[7px] p-1 text-shell-text-secondary hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <MinusIcon size={13} />
              </button>
              <select
                aria-label="Zoom level"
                value={zoomPct}
                onChange={(e) => setZoomCentered(Number(e.target.value) / 100)}
                className="rounded-[7px] border border-shell-border bg-shell-surface px-1.5 py-[3px] text-[11px] font-mono text-shell-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                {!(ZOOM_STEPS as readonly number[]).includes(zoom) && (
                  <option value={zoomPct}>{zoomPct}%</option>
                )}
                {ZOOM_STEPS.map((z) => (
                  <option key={z} value={Math.round(z * 100)}>
                    {Math.round(z * 100)}%
                  </option>
                ))}
              </select>
              <button
                type="button"
                aria-label="Zoom in"
                onClick={() => setZoomCentered(zoom * 1.25)}
                className="rounded-[7px] p-1 text-shell-text-secondary hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <PlusIcon size={13} />
              </button>
              <button
                type="button"
                aria-label="Fit to screen"
                title="Fit"
                onClick={() => fitToScreen()}
                className="rounded-[7px] p-1 text-shell-text-secondary hover:bg-shell-surface-active hover:text-shell-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              >
                <Maximize size={13} />
              </button>
            </div>
          </div>

          {/* artboard */}
          <div ref={containerRef} className="relative flex flex-1 items-center justify-center overflow-hidden">
            {elements.length === 0 && (
              <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center px-6 text-center text-[13px] text-shell-text-tertiary">
                Add an element or generate with Magic
              </div>
            )}
            {stageSize.width > 0 && (
              <Stage
                ref={stageRef}
                width={stageSize.width}
                height={stageSize.height}
                x={stagePos.x}
                y={stagePos.y}
                scaleX={zoom}
                scaleY={zoom}
                draggable={isSpaceDown}
                onWheel={handleWheel}
                onMouseDown={handleStageMouseDown}
                onDragEnd={(e) => {
                  if (e.target === e.target.getStage()) {
                    setStagePos(e.target.position());
                  }
                }}
              >
                <Layer id="design-content-layer">
                  <Rect
                    name="artboard-background"
                    x={0}
                    y={0}
                    width={artboard.width}
                    height={artboard.height}
                    fillLinearGradientStartPoint={{ x: 0, y: 0 }}
                    fillLinearGradientEndPoint={{ x: artboard.width, y: artboard.height }}
                    fillLinearGradientColorStops={[0, "#2c3142", 1, "#171a24"]}
                    onClick={() => setSelectedId(null)}
                    onTap={() => setSelectedId(null)}
                  />
                  {[...elements]
                    .sort((a, b) => a.zIndex - b.zIndex)
                    .map((el) => (
                      <CanvasNode
                        key={el.id}
                        element={el}
                        isSelected={el.id === selectedId}
                        onSelect={setSelectedId}
                        onDragEnd={patchElement}
                        onTransformEnd={patchElement}
                        onDblClick={beginEditText}
                        registerNode={registerNode}
                      />
                    ))}
                </Layer>
                <Layer listening>
                  <Transformer ref={transformerRef} rotateEnabled flipEnabled={false} />
                </Layer>
              </Stage>
            )}

            {editingId && editorStyle && (
              <textarea
                aria-label="Edit text"
                autoFocus
                value={editingValue}
                onChange={(e) => setEditingValue(e.target.value)}
                onBlur={commitTextEdit}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    commitTextEdit();
                  }
                  if (e.key === "Escape") {
                    setEditingId(null);
                  }
                }}
                style={{
                  ...editorStyle,
                  background: "transparent",
                  border: "1px dashed rgba(255,255,255,0.5)",
                  outline: "none",
                  resize: "none",
                  lineHeight: 1.1,
                  padding: 0,
                  zIndex: 20,
                }}
              />
            )}
          </div>
        </div>

        {/* right properties panel */}
        <div className="flex w-full flex-none flex-col gap-4 overflow-auto border-t border-shell-border p-4 md:w-[248px] md:border-l md:border-t-0">
          <PropertiesPanel
            selected={selected}
            onUpdate={updateSelected}
            onDuplicate={duplicateSelected}
            onDelete={() => selectedId && deleteElement(selectedId)}
          />
        </div>
      </div>
    </>
  );
}
