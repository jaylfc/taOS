/**
 * Fetch wrapper for /api/desktop/browser/suggest.
 * Local-only suggestions: history + bookmarks per (user, profile).
 */

export interface Suggestion {
  url: string;
  title: string;
  source: "history" | "bookmark" | "open-tab";
  score: number;
}

export async function fetchSuggestions(
  profileId: string,
  q: string,
  limit: number = 8,
): Promise<Suggestion[]> {
  if (!q.trim()) return [];

  const params = new URLSearchParams({
    profile_id: profileId,
    q,
    limit: String(limit),
  });
  try {
    const resp = await fetch(
      `/api/desktop/browser/suggest?${params.toString()}`,
      { credentials: "include" },
    );
    if (!resp.ok) return [];
    const body = await resp.json();
    return Array.isArray(body?.suggestions) ? body.suggestions : [];
  } catch {
    return [];
  }
}
