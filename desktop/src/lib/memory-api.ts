/* ------------------------------------------------------------------ */
/*  Memory model API helpers                                           */
/* ------------------------------------------------------------------ */

async function throwOnError(res: Response): Promise<Response> {
  if (res.ok) return res;
  let detail = `Request failed (${res.status})`;
  try {
    const body = await res.json();
    if (body.detail) detail = String(body.detail);
    else if (body.error) detail = String(body.error);
  } catch { /* ignore parse error */ }
  throw new Error(detail);
}

export async function fetchMemoryModel(): Promise<{ model: string | null; supported: boolean }> {
  const res = await fetch("/api/memory/model", { headers: { Accept: "application/json" } });
  await throwOnError(res);
  return res.json();
}

export async function setMemoryModel(
  args: { model?: string; clear?: boolean },
): Promise<{ model: string | null }> {
  const res = await fetch("/api/memory/model", {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(args),
  });
  await throwOnError(res);
  return res.json();
}
