/** Client for POST /api/userspace-apps/analyze -- the static security analyzer. */

export type Severity = "critical" | "warning" | "info";

export interface Finding {
  severity: Severity;
  rule_id: string;
  file: string;
  line: number;
  message: string;
}

export interface AnalyzeResult {
  findings: Finding[];
  blocked: boolean;
}

/** Run the server-side analyzer over a map of filename -> source text.
 *
 * This is a preview convenience for App Studio's Build/Publish views -- the
 * authoritative, unbypassable gate runs server-side inside the install/
 * publish route itself, so a stale or spoofed client-side result here can
 * never let a critical finding through to a real install.
 */
export async function analyzeAppSource(files: Record<string, string>): Promise<AnalyzeResult> {
  const resp = await fetch("/api/userspace-apps/analyze", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ files }),
  });
  if (!resp.ok) {
    const raw = await resp.text().catch(() => "");
    throw new Error(raw || `analysis request failed (${resp.status})`);
  }
  return resp.json();
}
