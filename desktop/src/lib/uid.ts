/**
 * `crypto.randomUUID()` only exists in a secure context (https or localhost).
 * taOS is commonly accessed over plain http (e.g. http://taos.local:6969),
 * where `crypto.randomUUID` is undefined and calling it throws synchronously.
 * Use this helper anywhere a unique id is needed instead of calling
 * `crypto.randomUUID()` directly.
 */
export function randomId(prefix?: string): string {
  const id =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : Date.now().toString(36) + Math.random().toString(36).slice(2);
  return prefix ? `${prefix}${id}` : id;
}
