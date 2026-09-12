// desktop/src/apps/chat/AttachmentGallery.tsx
import { useState } from "react";
import type { AttachmentRecord } from "@/lib/chat-attachments-api";
import { DocChip } from "@/components/ui";
import { AttachmentLightbox } from "./AttachmentLightbox";

export function AttachmentGallery({ attachments }: { attachments: AttachmentRecord[] }) {
  const [lightboxStart, setLightboxStart] = useState<number | null>(null);
  if (!attachments?.length) return null;
  const images = attachments.filter((a) => a.mime_type?.startsWith("image/"));
  const files = attachments.filter((a) => !a.mime_type?.startsWith("image/"));

  const gridClass = images.length > 1 ? "grid grid-cols-2 gap-1 max-w-md" : "";

  return (
    <div className="flex flex-col gap-2 mt-1">
      {images.length > 0 && (
        <div className={gridClass}>
          {images.slice(0, 4).map((img, i) => (
            <button key={img.url} onClick={() => setLightboxStart(i)} className="relative block">
              <img
                src={img.url}
                alt={img.filename}
                className={images.length === 1
                  ? "max-w-[560px] max-h-[400px] rounded"
                  : "object-cover w-full h-32 rounded"}
              />
              {images.length > 4 && i === 3 && (
                <span className="absolute inset-0 bg-black/60 flex items-center justify-center text-white">
                  +{images.length - 4} more
                </span>
              )}
            </button>
          ))}
        </div>
      )}
{files.length > 0 && (
        <div className="flex flex-col gap-1">
          {files.map((f) => (
            <DocChip
              key={f.url}
              name={f.filename}
              fileSize={f.size ?? 0}
              inFiles={false}
              onFetch={() => window.open(f.url, "_blank")}
            />
          ))}
        </div>
      )}
      {lightboxStart !== null && (
        <AttachmentLightbox
          images={images}
          startIndex={lightboxStart}
          onClose={() => setLightboxStart(null)}
        />
      )}
    </div>
  );
}
