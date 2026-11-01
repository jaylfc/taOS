/**
 * Which on-device chat model setup offers. GET /api/models lists the catalog
 * with each variant's fit for this device's RAM ("green" = comfortable).
 *
 * PREFERRED is an interim order: the phone's default is being chosen by the
 * model x harness tool-calling bench, and its winner goes first here once
 * it is in the catalog. Until then, small models with good tool calling lead.
 */
export const PREFERRED = ["qwen3-1.7b", "gemma-3-1b", "llama-3.2-1b", "qwen2.5-1.5b"];

export interface CatalogVariant {
  id: string;
  name?: string;
  format?: string;
  size_mb?: number;
  compatibility?: string;
  downloaded?: boolean;
  requires?: { backends?: { id?: string }[] };
}

export interface CatalogModel {
  id: string;
  name: string;
  capabilities?: string[];
  hardware_tiers?: Record<string, { recommended?: string } | string>;
  variants: CatalogVariant[];
}

export interface OnDeviceChoice {
  app_id: string;
  variant_id: string;
  name: string;
  size_mb: number;
  downloaded: boolean;
}

/** A GGUF that fits comfortably; NPU-only builds (rkllama) need their own runtime. */
function usable(v: CatalogVariant): boolean {
  const npu = (v.requires?.backends ?? []).some((b) => b.id === "rkllama");
  return v.format === "gguf" && v.compatibility === "green" && !npu;
}

function variantFor(m: CatalogModel, profileId: string): CatalogVariant | undefined {
  const tier = m.hardware_tiers?.[profileId];
  const named = typeof tier === "object" ? tier?.recommended : undefined;
  const pick = m.variants.find((v) => v.id === named);
  if (pick && usable(pick)) return pick;
  return m.variants.find(usable);
}

export function pickOnDeviceModel(models: CatalogModel[], profileId: string): OnDeviceChoice | null {
  const chat = models.filter((m) => m.capabilities?.includes("chat"));
  const rank = (m: CatalogModel) => {
    const i = PREFERRED.indexOf(m.id);
    return i === -1 ? PREFERRED.length : i;
  };
  const ordered = [...chat].sort((a, b) => {
    const r = rank(a) - rank(b);
    if (r !== 0) return r;
    // Outside the preferred list: the smallest model that fits.
    return (variantFor(a, profileId)?.size_mb ?? Infinity) - (variantFor(b, profileId)?.size_mb ?? Infinity);
  });
  for (const m of ordered) {
    const v = variantFor(m, profileId);
    if (v) {
      return {
        app_id: m.id,
        variant_id: v.id,
        name: m.name,
        size_mb: v.size_mb ?? 0,
        downloaded: Boolean(v.downloaded),
      };
    }
  }
  return null;
}

export function formatSize(mb: number): string {
  return mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

export function parseChoice(raw: unknown): OnDeviceChoice | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const r = raw as Record<string, unknown>;
  if (typeof r.app_id !== "string" || typeof r.variant_id !== "string") return undefined;
  return {
    app_id: r.app_id,
    variant_id: r.variant_id,
    name: typeof r.name === "string" ? r.name : r.app_id,
    size_mb: typeof r.size_mb === "number" ? r.size_mb : 0,
    downloaded: r.downloaded === true,
  };
}
