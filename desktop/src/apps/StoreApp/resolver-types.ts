/**
 * Mirrors of the Python resolver types in tinyagentos/catalog/resolver.py.
 * The /api/store/resolve endpoint returns one of ResolveOkResp | ResolveErrResp
 * wrapped in an envelope. Keep these in sync if the Python types change.
 */

export interface BackendDep {
  id: string;
  targets: string[];
  min_ram_mb: number;
  min_vram_mb?: number;
}

export type Compat = "green" | "amber" | "red";

export interface ResolveOkResp {
  result: "ok";
  backend_id: string;
  variant_id: string;
  action: "use" | "install_chain";
  compat: Compat;
}

export interface ResolveErrResp {
  result: "err";
  reason: string;
  near_miss: {
    variant?: string;
    blocked_by?: "ram" | "vram" | "disk" | "target" | "schema";
    short_by_mb?: number;
  };
  suggestions: string[];
  compat: Compat;
}

export type ResolveResponse = ResolveOkResp | ResolveErrResp;

/** POST /api/store/resolve helper. */
export async function resolveModel(
  manifestId: string,
  variantId: string = "auto",
  options: { targetRemote?: string; force?: boolean } = {},
): Promise<ResolveResponse> {
  const res = await fetch("/api/store/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      manifest_id: manifestId,
      variant_id: variantId,
      target_remote: options.targetRemote ?? null,
      force: options.force ?? false,
    }),
  });
  return res.json();
}
