/** Thin client for the preview/package endpoints of tinyagentos/routes/web.py.
 *  CRUD (create/list/get/update/delete) stays inline in WebStudioApp.tsx,
 *  matching the existing pattern; this module only covers the two routes
 *  added for the sandboxed preview and the install/export package (mirrors
 *  gamestudio/games-api.ts's gamePreviewUrl/gamePackageUrl/fetchGamePackage). */

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (body?.error) return String(body.error);
  } catch {
    // non-JSON error body; fall through to the status-based message
  }
  return `Request failed (${res.status})`;
}

export interface SiteRow {
  id: string;
  title: string;
  content: string;
  index_html: string;
  created_at: number;
  updated_at: number;
}

/** Fetch a saved site's full row (including its rendered index_html), used
 *  by ShareView to run the security analyzer over the exported HTML. */
export async function getSiteRow(id: string): Promise<SiteRow> {
  const res = await fetch(`/api/web/sites/${encodeURIComponent(id)}`, { credentials: "include" });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as SiteRow;
}

export function sitePreviewUrl(id: string): string {
  return `/api/web/sites/${encodeURIComponent(id)}/preview`;
}

export function sitePackageUrl(id: string): string {
  return `/api/web/sites/${encodeURIComponent(id)}/package`;
}

/** Fetch a site's .taosapp package as a downloadable File -- used both to
 *  install it (POST to /api/userspace-apps/install) and to export it. */
export async function fetchSitePackage(id: string): Promise<File> {
  const res = await fetch(sitePackageUrl(id));
  if (!res.ok) throw new Error(await readError(res));
  const blob = await res.blob();
  return new File([blob], `${id}.taosapp`, { type: "application/zip" });
}
