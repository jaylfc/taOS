// desktop/src/apps/chat/AttachmentLightbox.tsx
import { useEffect, useRef, useState } from "react";
import type { AttachmentRecord } from "@/lib/chat-attachments-api";

const MIN_ZOOM = 1;
const MAX_ZOOM = 4;

export function AttachmentLightbox({
  images, startIndex, onClose,
}: {
  images: AttachmentRecord[];
  startIndex: number;
  onClose: () => void;
}) {
  const [idx, setIdx] = useState(startIndex);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ startX: number; startY: number; panX: number; panY: number } | null>(null);

  const resetZoom = () => { setZoom(1); setPan({ x: 0, y: 0 }); };

  const clampZoom = (z: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));

  const navigate = (delta: number) => {
    setIdx((i) => Math.max(0, Math.min(images.length - 1, i + delta)));
    resetZoom();
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft") navigate(-1);
      if (e.key === "ArrowRight") navigate(1);
      if (e.key === "+" || e.key === "=") setZoom((z) => clampZoom(z * 1.2));
      if (e.key === "-") setZoom((z) => clampZoom(z / 1.2));
      if (e.key === "0") resetZoom();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [images.length, onClose]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const delta = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    setZoom((z) => clampZoom(z * delta));
  };

  const handleDoubleClick = () => {
    setZoom((z) => z > 1 ? 1 : 2);
    setPan({ x: 0, y: 0 });
  };

  const handlePointerDown = (e: React.PointerEvent<HTMLImageElement>) => {
    if (zoom <= 1) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    dragRef.current = { startX: e.clientX, startY: e.clientY, panX: pan.x, panY: pan.y };
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLImageElement>) => {
    if (!dragRef.current) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;
    setPan({ x: dragRef.current.panX + dx, y: dragRef.current.panY + dy });
  };

  const handlePointerUp = () => { dragRef.current = null; };

  const current = images[idx]!;
  return (
    <div
      role="dialog"
      aria-label="Image viewer"
      className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center"
      onClick={onClose}
      onWheel={handleWheel}
    >
      <img
        src={current.url}
        alt={current.filename}
        className="max-w-[90vw] max-h-[90vh] select-none"
        style={{
          transform: `scale(${zoom}) translate(${pan.x / zoom}px, ${pan.y / zoom}px)`,
          cursor: zoom > 1 ? "grab" : "default",
          transition: dragRef.current ? "none" : "transform 0.1s ease",
        }}
        onClick={(e) => e.stopPropagation()}
        onDoubleClick={handleDoubleClick}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        draggable={false}
      />
      <div className="absolute top-4 right-4 flex gap-2" onClick={(e) => e.stopPropagation()}>
        {zoom !== 1 && (
          <button
            onClick={resetZoom}
            className="bg-white/10 hover:bg-white/20 rounded px-3 py-1 text-sm"
            aria-label="Reset zoom"
          >
            {Math.round(zoom * 100)}%
          </button>
        )}
        <a href={current.url} download={current.filename}
           onClick={(e) => e.stopPropagation()}
           className="bg-white/10 hover:bg-white/20 rounded px-3 py-1 text-sm">Download</a>
        <button onClick={onClose} className="bg-white/10 hover:bg-white/20 rounded px-3 py-1 text-sm">Close</button>
      </div>
      {images.length > 1 && (
        <>
          <button
            aria-label="Previous image"
            onClick={(e) => { e.stopPropagation(); navigate(-1); }}
            className="absolute left-4 top-1/2 -translate-y-1/2 bg-white/10 hover:bg-white/20 rounded-full w-9 h-9 flex items-center justify-center text-lg"
          >‹</button>
          <button
            aria-label="Next image"
            onClick={(e) => { e.stopPropagation(); navigate(1); }}
            className="absolute right-4 top-1/2 -translate-y-1/2 bg-white/10 hover:bg-white/20 rounded-full w-9 h-9 flex items-center justify-center text-lg"
          >›</button>
          <div className="absolute bottom-4 text-white/70 text-xs">{idx + 1} / {images.length}</div>
        </>
      )}
    </div>
  );
}
