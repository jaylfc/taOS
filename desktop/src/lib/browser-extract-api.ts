/**
 * Fetch wrapper for /api/desktop/browser/extract.
 * Runs Mozilla Readability on the backend and returns structured article data.
 * Returns null on any non-2xx response (silent on 401, matching other browser-* wrappers).
 */

export interface ExtractResult {
  title: string;
  text: string;
  html: string;
  word_count: number;
  note?: string;
}

export async function extractReadable(
  profileId: string,
  url: string,
): Promise<ExtractResult | null> {
  const params = new URLSearchParams({ profile_id: profileId, url });
  try {
    const resp = await fetch(
      `/api/desktop/browser/extract?${params.toString()}`,
      { credentials: "include" },
    );
    if (!resp.ok) return null;
    return (await resp.json()) as ExtractResult;
  } catch {
    return null;
  }
}
