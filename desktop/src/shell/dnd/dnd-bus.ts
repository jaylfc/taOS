import type { DragPayload } from "./types";

let current: DragPayload | null = null;
const emitter = new EventTarget();
let staleTimer: ReturnType<typeof setTimeout> | null = null;

const STALE_MS = 30_000;

function emit() {
  emitter.dispatchEvent(new Event("change"));
}

export function startDrag(payload: DragPayload): void {
  current = payload;
  if (staleTimer) clearTimeout(staleTimer);
  staleTimer = setTimeout(() => {
    current = null;
    staleTimer = null;
    emit();
  }, STALE_MS);
  emit();
}

export function endDrag(): void {
  if (current === null && staleTimer === null) return;
  current = null;
  if (staleTimer) {
    clearTimeout(staleTimer);
    staleTimer = null;
  }
  emit();
}

export function getCurrent(): DragPayload | null {
  return current;
}

export function subscribe(listener: () => void): () => void {
  emitter.addEventListener("change", listener);
  return () => emitter.removeEventListener("change", listener);
}
