/**
 * Which on-device chat model setup offers. GET /api/models lists the catalog
 * with each variant's fit for this device's RAM ("green" = comfortable).
 *
 * PREFERRED is an interim order: the phone's default is being chosen by the
 * model x harness tool-calling bench, and its winner goes first here once
 * it is in the catalog. Until then, small models with good tool calling lead.
 */
import { HopError } from "./agent";

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
  /** The download setup started, while it is still running. */
  download_id?: string;
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
    ...(typeof r.download_id === "string" ? { download_id: r.download_id } : {}),
  };
}

/**
 * Waits for the on-device model's download to finish, so nothing is set up
 * to run on a model that is not there yet. Without a recorded id it uses the
 * one POST /api/models/download derives (app_id-variant_id). No record at all
 * (a restart dropped it) ends the wait: the deploy then says whether the
 * model is there.
 *
 * Repeated failed checks end the wait with an error after 20 consecutive polls.
 */
export async function waitForDownload(
  choice: OnDeviceChoice,
  onProgress: (percent: number) => void,
  pollMs = 3000,
): Promise<void> {
  if (choice.downloaded) return;
  const id = choice.download_id ?? `${choice.app_id}-${choice.variant_id}`;
  let failedPolls = 0;
  for (;;) {
    let task: { status?: string; percent?: number; error?: string } | undefined;
    try {
      const res = await fetch(`/api/models/downloads/${encodeURIComponent(id)}`, { credentials: "include" });
      if (res.status === 404) return;
      if (res.ok) task = await res.json();
    } catch {
      /* a missed poll is retried on the next tick */
    }
    failedPolls = task ? 0 : failedPolls + 1;
    if (failedPolls >= 20) throw new HopError(`Couldn't check the ${choice.name} download. Try again.`);
    if (task?.status === "complete") return;
    if (task?.status === "error") throw new HopError(`${choice.name} didn't finish downloading: ${task.error || "the download failed"}.`);
    if (task) onProgress(Number(task.percent) || 0);
    await new Promise((r) => setTimeout(r, pollMs));
  }
}
