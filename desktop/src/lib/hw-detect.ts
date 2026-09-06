export type HwClass = "rk3588" | "gpu" | "cpu";

let _cached: HwClass | null = null;

export async function detectHwClass(): Promise<HwClass> {
  if (_cached) return _cached;
  try {
    const r = await fetch("/api/cluster/workers");
    if (!r.ok) { _cached = "cpu"; return _cached; }
    const workers: Array<{ capabilities?: string[] }> = await r.json();
    for (const w of Array.isArray(workers) ? workers : []) {
      const caps: string[] = w?.capabilities || [];
      if (caps.some((c) => /rk3588|npu/i.test(c))) { _cached = "rk3588"; return _cached; }
      if (caps.some((c) => /gpu|cuda|rocm|metal/i.test(c))) { _cached = "gpu"; return _cached; }
    }
    _cached = "cpu";
    return _cached;
  } catch {
    _cached = "cpu";
    return _cached;
  }
}
