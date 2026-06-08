/**
 * Lightweight cross-component event bus for app-lifecycle events.
 *
 * Pattern mirrors dnd-bus.ts — uses a module-level EventTarget so all
 * subscribers in the same page share one instance regardless of React tree.
 *
 * Current events:
 *   app.installed  — fired after a store install succeeds
 */

const _emitter = new EventTarget();

/** Emit a named event with an optional string payload. */
export function emitAppEvent(name: string, detail?: string): void {
  _emitter.dispatchEvent(
    new CustomEvent(name, detail !== undefined ? { detail } : undefined)
  );
}

/** Subscribe to a named event. Returns an unsubscribe function. */
export function onAppEvent(
  name: string,
  listener: (detail?: string) => void
): () => void {
  const handler = (e: Event) =>
    listener((e as CustomEvent<string | undefined>).detail);
  _emitter.addEventListener(name, handler);
  return () => _emitter.removeEventListener(name, handler);
}

/** Event name emitted on successful app install. */
export const APP_INSTALLED = "app.installed";
