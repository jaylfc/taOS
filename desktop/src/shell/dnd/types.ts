export type DragPayload =
  | { kind: "file"; path: string; mime_type: string; size: number; name: string }
  | { kind: "message"; channel_id: string; message_id: string; author_id: string; excerpt: string }
  | { kind: "knowledge"; id: string; title: string; url?: string }
  | { kind: "canvas-block"; canvas_id: string; block_id: string; block_type: string };

export type DragKind = DragPayload["kind"];
