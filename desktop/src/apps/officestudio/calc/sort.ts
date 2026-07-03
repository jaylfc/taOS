// Comparator used to sort a spreadsheet column. Numbers and numeric-looking
// strings (including scientific notation, e.g. "1e3") compare numerically
// when both sides parse as finite numbers; otherwise we fall back to a
// locale-aware string compare. Blank cells never coerce to 0 (Number("")
// would otherwise treat an empty cell as numeric zero), so they fall back to
// string compare and sort with the text values instead of among numbers.

export type SortDirection = "asc" | "desc";

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed === "") return null;
    const n = Number(trimmed);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

// Returns 0 for equal values, so the native (stable, ES2019+) Array.sort
// preserves the original relative order of equal rows.
export function compareCellValues(a: unknown, b: unknown, direction: SortDirection): number {
  const an = toFiniteNumber(a);
  const bn = toFiniteNumber(b);
  const cmp =
    an != null && bn != null ? an - bn : String(a ?? "").localeCompare(String(b ?? ""));
  return direction === "asc" ? cmp : -cmp;
}
