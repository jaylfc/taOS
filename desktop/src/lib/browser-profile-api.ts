/**
 * Fetch wrappers for /api/desktop/browser/profiles CRUD.
 *
 * 401 returns empty array / null silently (the user isn't logged in yet).
 * Other errors throw — callers should handle them.
 */

export interface Profile {
  profile_id: string;
  name: string;
  color: string | null;
  created_at: number;
}

const ENDPOINT = "/api/desktop/browser/profiles";

export async function listProfiles(): Promise<Profile[]> {
  try {
    const resp = await fetch(ENDPOINT, { credentials: "include" });
    if (resp.status === 401) return [];
    if (!resp.ok) return [];
    const body = await resp.json();
    return Array.isArray(body?.profiles) ? body.profiles : [];
  } catch {
    return [];
  }
}

export async function createProfile(args: {
  name: string;
  color?: string;
}): Promise<Profile | null> {
  const resp = await fetch(ENDPOINT, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: args.name, color: args.color ?? null }),
  });
  if (!resp.ok) return null;
  return resp.json();
}

export async function renameProfile(
  profileId: string,
  args: { name?: string; color?: string },
): Promise<Profile | null> {
  const resp = await fetch(
    `${ENDPOINT}/${encodeURIComponent(profileId)}`,
    {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(args),
    },
  );
  if (!resp.ok) return null;
  return resp.json();
}

export async function deleteProfile(profileId: string): Promise<boolean> {
  const resp = await fetch(
    `${ENDPOINT}/${encodeURIComponent(profileId)}`,
    { method: "DELETE", credentials: "include" },
  );
  return resp.ok;
}
