/* ------------------------------------------------------------------ */
/*  Base Images: pure client-side helpers                              */
/*                                                                     */
/*  Byte-size formatting and the GET /api/agent-images response ->     */
/*  row mapping for the Base Images pane. Kept framework-free so they  */
/*  are unit-testable (mirrors importBundle.ts / a2aSelection.ts).     */
/* ------------------------------------------------------------------ */

/** One base image row rendered by the pane. */
export interface BaseImageRow {
  alias: string;
  architecture: string;
  /** Raw incus size string, e.g. "412.50MiB". */
  size: string;
  sizeBytes: number;
  uploadedAt: string;
  /** Framework the alias backs, or null for the generic taos-base. */
  framework: string | null;
}

/** Parsed shape of GET /api/agent-images. */
export interface BaseImagesView {
  images: BaseImageRow[];
  totalSizeBytes: number;
  prefetchEnabled: boolean;
  incusAvailable: boolean;
}

/**
 * Human-readable byte size using binary (1024) units, e.g. "1.2 GB".
 * Best-effort: a negative or non-finite value renders as "0 B".
 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  // Whole bytes need no decimal; larger units get one.
  const text = unit === 0 ? String(Math.round(value)) : value.toFixed(1);
  return `${text} ${units[unit]}`;
}

/** Display label for the framework an image backs (generic base has none). */
export function frameworkLabel(framework: string | null): string {
  return framework ?? "generic";
}

/**
 * Map the raw GET /api/agent-images JSON into the view the pane renders.
 * Unknown/missing fields degrade to safe defaults so a malformed entry can
 * never crash the list.
 */
export function parseBaseImagesResponse(data: unknown): BaseImagesView {
  const obj = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const rawImages = Array.isArray(obj.images) ? obj.images : [];

  const images: BaseImageRow[] = rawImages.map((entry) => {
    const e = (entry && typeof entry === "object" ? entry : {}) as Record<string, unknown>;
    return {
      alias: String(e.alias ?? ""),
      architecture: String(e.architecture ?? ""),
      size: String(e.size ?? ""),
      sizeBytes: typeof e.size_bytes === "number" ? e.size_bytes : 0,
      uploadedAt: String(e.uploaded_at ?? ""),
      framework: e.framework == null ? null : String(e.framework),
    };
  });

  return {
    images,
    totalSizeBytes: typeof obj.total_size_bytes === "number" ? obj.total_size_bytes : 0,
    prefetchEnabled: Boolean(obj.prefetch_enabled),
    incusAvailable: Boolean(obj.incus_available),
  };
}
