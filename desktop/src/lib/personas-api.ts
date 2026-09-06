import type { PersonaSource, PersonaSummary } from "@/components/persona-picker/types";

export async function fetchLibrary(opts: {
  source?: PersonaSource;
  q?: string;
  limit?: number;
  offset?: number;
}): Promise<PersonaSummary[]> {
  const qs = new URLSearchParams();
  if (opts.source) qs.set("source", opts.source);
  if (opts.q) qs.set("q", opts.q);
  if (opts.limit) qs.set("limit", String(opts.limit));
  if (opts.offset) qs.set("offset", String(opts.offset));
  const res = await fetch(`/api/personas/library?${qs}`);
  const j = await res.json();
  return j.personas as PersonaSummary[];
}

/**
 * Fetch full persona detail. The unified `/api/personas/library/{source}/{id}` endpoint
 * does not exist, so we dispatch on source:
 *   - builtin / awesome-openclaw / prompt-library → GET /api/templates/{id}
 *     (server-side get_template searches builtin + vendored by id)
 *   - user    → GET /api/user-personas/{id} (returns { id, name, soul_md, agent_md, ... })
 */
export async function fetchPersonaDetail(
  source: string,
  id: string,
): Promise<{ soul_md: string; agent_md?: string; name: string; source: string; id: string }> {
  if (source === "builtin" || source === "awesome-openclaw" || source === "prompt-library") {
    const res = await fetch(`/api/templates/${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error(`Template not found: ${id}`);
    const j = await res.json();
    return {
      id: j.id,
      name: j.name,
      source,
      soul_md: j.system_prompt ?? "",
      agent_md: undefined,
    };
  }

  if (source === "user") {
    const res = await fetch(`/api/user-personas/${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error(`User persona not found: ${id}`);
    const j = await res.json();
    return {
      id: j.id,
      name: j.name,
      source: "user",
      soul_md: j.soul_md ?? "",
      agent_md: j.agent_md,
    };
  }

  throw new Error(`Unsupported source for detail fetch: ${source}`);
}
