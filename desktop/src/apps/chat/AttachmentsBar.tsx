import type { AttachmentRecord } from "@/lib/chat-attachments-api";

export type PendingAttachment = {
  id: string;
  filename: string;
  size: number;
  mime_type?: string;
  record?: AttachmentRecord;  // set once upload completes
  error?: string;
  uploading?: boolean;
  file?: File;        // original file kept so a failed upload can be retried
  retries?: number;   // retry attempts so far (capped at 3)
};

export function AttachmentsBar({
  items,
  onRemove,
  onRetry,
}: {
  items: PendingAttachment[];
  onRemove: (id: string) => void;
  onRetry: (id: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div
      aria-label="Pending attachments"
      className="px-4 py-2 border-t border-white/10 flex gap-2 flex-wrap"
    >
      {items.map((it) => (
        <div key={it.id} className="flex items-center gap-2 bg-white/5 rounded px-2 py-1 text-xs max-w-[220px]">
          <span className="truncate">{it.filename}</span>
          <span className="opacity-50">{Math.max(1, Math.round(it.size / 1024))} KB</span>
          {it.uploading && <span className="opacity-70">…</span>}
          {it.error && (
            (it.retries ?? 0) < 3 && it.file ? (
              <button aria-label="Retry upload" onClick={() => onRetry(it.id)} className="text-red-300">retry</button>
            ) : (
              <span title="Upload failed" className="text-red-400/70">failed</span>
            )
          )}
          <button aria-label={`Remove ${it.filename}`} onClick={() => onRemove(it.id)} className="opacity-70 hover:opacity-100">×</button>
        </div>
      ))}
    </div>
  );
}
