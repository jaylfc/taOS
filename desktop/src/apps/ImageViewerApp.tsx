import { useState, useRef, useCallback, useEffect } from "react";
import { ZoomIn, ZoomOut, RotateCw, Maximize } from "lucide-react";

const ZOOM_STEP = 0.25;
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 5;

export function ImageViewerApp({ windowId: _windowId, url }: { windowId: string; url?: string }) {
  const [imageUrl, setImageUrl] = useState<string | null>(url ?? null);
  const [fileName, setFileName] = useState("");
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    if (!url) return;
    setImageUrl(url);
    const path = decodeURIComponent(url.split("/").pop() ?? "");
    setFileName(path.split("/").pop() ?? "");
  }, [url]);

  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    if (imageUrl) URL.revokeObjectURL(imageUrl);

    const url = URL.createObjectURL(file);
    setImageUrl(url);
    setFileName(file.name);
    setZoom(1);
    setRotation(0);
  }

  const zoomIn = useCallback(() => {
    setZoom((z) => Math.min(z + ZOOM_STEP, MAX_ZOOM));
  }, []);

  const zoomOut = useCallback(() => {
    setZoom((z) => Math.max(z - ZOOM_STEP, MIN_ZOOM));
  }, []);

  const rotate = useCallback(() => {
    setRotation((r) => (r + 90) % 360);
  }, []);

  const fitToView = useCallback(() => {
    if (!containerRef.current || !imgRef.current) return;

    const container = containerRef.current.getBoundingClientRect();
    const img = imgRef.current;
    const naturalW = img.naturalWidth;
    const naturalH = img.naturalHeight;

    if (!naturalW || !naturalH) return;

    const isRotated = rotation % 180 !== 0;
    const effectiveW = isRotated ? naturalH : naturalW;
    const effectiveH = isRotated ? naturalW : naturalH;

    const scaleX = container.width / effectiveW;
    const scaleY = container.height / effectiveH;
    setZoom(Math.min(scaleX, scaleY, MAX_ZOOM));
  }, [rotation]);

  const zoomPercent = Math.round(zoom * 100);

  return (
    <div className="flex flex-col h-full bg-shell-bg-deep select-none">
      {!imageUrl ? (
        <div className="flex flex-col items-center justify-center flex-1 gap-4 p-8">
          <div className="text-shell-text-secondary text-lg">
            No image loaded
          </div>
          <label
            className="px-4 py-2 rounded-lg bg-accent text-white cursor-pointer hover:bg-accent/90 transition-colors"
            aria-label="Choose image file"
          >
            Open Image
            <input
              type="file"
              accept="image/*"
              onChange={handleFileSelect}
              className="hidden"
            />
          </label>
        </div>
      ) : (
        <>
          {/* Toolbar */}
          <div className="flex items-center gap-1 px-3 py-2 bg-shell-surface/50 border-b border-white/10">
            <span className="text-shell-text text-sm truncate flex-1 mr-2">
              {fileName}
            </span>
            <button
              onClick={zoomOut}
              className="p-1.5 rounded hover:bg-shell-surface transition-colors text-shell-text-secondary hover:text-shell-text"
              aria-label="Zoom out"
              title="Zoom out"
            >
              <ZoomOut size={18} />
            </button>
            <span
              className="text-shell-text-secondary text-xs w-12 text-center tabular-nums"
              aria-label={`Zoom level ${zoomPercent}%`}
            >
              {zoomPercent}%
            </span>
            <button
              onClick={zoomIn}
              className="p-1.5 rounded hover:bg-shell-surface transition-colors text-shell-text-secondary hover:text-shell-text"
              aria-label="Zoom in"
              title="Zoom in"
            >
              <ZoomIn size={18} />
            </button>
            <div className="w-px h-5 bg-white/10 mx-1" />
            <button
              onClick={rotate}
              className="p-1.5 rounded hover:bg-shell-surface transition-colors text-shell-text-secondary hover:text-shell-text"
              aria-label="Rotate 90 degrees"
              title="Rotate"
            >
              <RotateCw size={18} />
            </button>
            <button
              onClick={fitToView}
              className="p-1.5 rounded hover:bg-shell-surface transition-colors text-shell-text-secondary hover:text-shell-text"
              aria-label="Fit to view"
              title="Fit to view"
            >
              <Maximize size={18} />
            </button>
            <div className="w-px h-5 bg-white/10 mx-1" />
            <label
              className="px-3 py-1 rounded text-xs bg-shell-surface text-shell-text-secondary cursor-pointer hover:bg-shell-surface/80 transition-colors"
              aria-label="Choose a different image"
            >
              Open
              <input
                type="file"
                accept="image/*"
                onChange={handleFileSelect}
                className="hidden"
              />
            </label>
          </div>

          {/* Image canvas */}
          <div
            ref={containerRef}
            className="flex-1 overflow-auto flex items-center justify-center min-h-0 bg-shell-bg-deep"
          >
            <img
              ref={imgRef}
              src={imageUrl}
              alt={fileName}
              className="transition-transform duration-150"
              style={{
                transform: `scale(${zoom}) rotate(${rotation}deg)`,
                transformOrigin: "center center",
              }}
              draggable={false}
            />
          </div>
        </>
      )}
    </div>
  );
}
