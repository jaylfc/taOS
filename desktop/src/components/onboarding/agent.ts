/**
 * Connecting a cloud model for the taOS Agent: save the API key, add the
 * provider, read the models it lists, then set the agent's model. Each hop
 * can fail on its own, so each failure says which one it was.
 *
 * Names follow Providers (apps/ProvidersApp.tsx): the provider is named after
 * its type and its key is the secret "provider-<name>-key", so what setup adds
 * is the same entry Providers shows and edits.
 */
import { CLOUD_PROVIDER_META } from "@/lib/cloud-providers";

/** Providers setup offers: those that need nothing but a key. */
export const SETUP_PROVIDERS = Object.entries(CLOUD_PROVIDER_META)
  .filter(([, meta]) => meta.url)
  .map(([type, meta]) => ({ type, ...meta }));

export class HopError extends Error {}

const JSON_HEADERS = { "Content-Type": "application/json", Accept: "application/json" };

async function reason(res: Response): Promise<string> {
  const data = await res.json().catch(() => ({}));
  return String(data?.error ?? data?.detail ?? `error ${res.status}`);
}

function label(type: string): string {
  return CLOUD_PROVIDER_META[type]?.label ?? type;
}

export function secretName(type: string): string {
  return `provider-${type}-key`;
}

/** Hop 1. A key saved on an earlier try (Back, or a resume) is replaced. */
export async function saveKey(type: string, key: string): Promise<void> {
  const name = secretName(type);
  try {
    let res = await fetch("/api/secrets", {
      method: "POST",
      headers: JSON_HEADERS,
      credentials: "include",
      body: JSON.stringify({
        name,
        value: key,
        category: "api-key",
        description: `API key for ${type} provider`,
        agents: [],
      }),
    });
    if (res.status === 409) {
      res = await fetch(`/api/secrets/${encodeURIComponent(name)}`, {
        method: "PUT",
        headers: JSON_HEADERS,
        credentials: "include",
        body: JSON.stringify({ value: key }),
      });
    }
    if (!res.ok) throw new HopError(`Couldn't save the API key: ${await reason(res)}.`);
  } catch (e) {
    if (e instanceof HopError) throw e;
    throw new HopError("Couldn't save the API key: taOS didn't answer.");
  }
}

/** Hop 2. A provider that already exists is used as it is. */
export async function addProvider(type: string): Promise<void> {
  try {
    const res = await fetch("/api/providers", {
      method: "POST",
      headers: JSON_HEADERS,
      credentials: "include",
      body: JSON.stringify({ name: type, type, api_key_secret: secretName(type) }),
    });
    if (!res.ok && res.status !== 409) {
      throw new HopError(`Couldn't add ${label(type)}: ${await reason(res)}.`);
    }
  } catch (e) {
    if (e instanceof HopError) throw e;
    throw new HopError(`Couldn't add ${label(type)}: taOS didn't answer.`);
  }
}

/** Hop 3. The models the provider entry lists (found when it was added). */
export async function providerModels(type: string): Promise<string[]> {
  let providers: unknown;
  try {
    const res = await fetch("/api/providers", { credentials: "include", headers: { Accept: "application/json" } });
    if (!res.ok) throw new HopError(`Couldn't read ${label(type)}'s models: ${await reason(res)}.`);
    providers = await res.json();
  } catch (e) {
    if (e instanceof HopError) throw e;
    throw new HopError(`Couldn't read ${label(type)}'s models: taOS didn't answer.`);
  }
  const entry = Array.isArray(providers)
    ? (providers as { name?: string; models?: unknown[] }[]).find((p) => p?.name === type)
    : undefined;
  const ids = (entry?.models ?? [])
    .map((m) => (typeof m === "string" ? m : (m as { id?: string; name?: string })?.id ?? (m as { name?: string })?.name))
    .filter((id): id is string => typeof id === "string" && id.length > 0);
  if (ids.length === 0) {
    throw new HopError(`${label(type)} didn't list any models. Check the key, or set this up later in Providers.`);
  }
  return ids;
}

/** Hop 4. */
export async function setAgentModel(model: string): Promise<void> {
  try {
    const res = await fetch("/api/taos-agent/settings", {
      method: "PATCH",
      headers: JSON_HEADERS,
      credentials: "include",
      body: JSON.stringify({ model }),
    });
    if (!res.ok) throw new HopError(`Couldn't give the taOS Agent this model: ${await reason(res)}.`);
  } catch (e) {
    if (e instanceof HopError) throw e;
    throw new HopError("Couldn't give the taOS Agent this model: taOS didn't answer.");
  }
}
