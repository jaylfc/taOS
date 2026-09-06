/**
 * Fetch wrappers for /api/desktop/browser/bookmarks.
 * All functions are silent on errors (return empty/null/false).
 */

export interface Bookmark {
  bookmark_id: string;
  url: string;
  title: string;
  created_at: number;
}

export async function listBookmarks(profileId: string): Promise<Bookmark[]> {
  const params = new URLSearchParams({ profile_id: profileId });
  try {
    const resp = await fetch(`/api/desktop/browser/bookmarks?${params}`, {
      credentials: "include",
    });
    if (!resp.ok) return [];
    const body = await resp.json();
    return Array.isArray(body?.bookmarks) ? body.bookmarks : [];
  } catch {
    return [];
  }
}

export async function addBookmark(
  profileId: string,
  url: string,
  title: string,
): Promise<string | null> {
  try {
    const resp = await fetch("/api/desktop/browser/bookmarks", {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ profile_id: profileId, url, title }),
    });
    if (!resp.ok) return null;
    const body = await resp.json();
    return typeof body?.bookmark_id === "string" ? body.bookmark_id : null;
  } catch {
    return null;
  }
}

export async function removeBookmark(
  profileId: string,
  bookmarkId: string,
): Promise<boolean> {
  const params = new URLSearchParams({ profile_id: profileId });
  try {
    const resp = await fetch(
      `/api/desktop/browser/bookmarks/${encodeURIComponent(bookmarkId)}?${params}`,
      { method: "DELETE", credentials: "include" },
    );
    return resp.ok;
  } catch {
    return false;
  }
}
