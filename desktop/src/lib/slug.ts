/**
 * Derive a slug from a free-form name. The single slug implementation on the
 * desktop side — nothing should keep a private copy of this regex.
 *
 * NFKD-normalising first folds accented Latin onto its base letters
 * ("résumé" -> "resume") instead of deleting them ("r-sum"). Scripts with no
 * ASCII decomposition (CJK, Cyrillic, Greek, …) still reduce to nothing here:
 * transliterating those needs a character table, which is a server-side job
 * (python-slugify). Returning "" for them is deliberate — a made-up client
 * slug would not match the one the server mints. Callers that must show or
 * send something use {@link slugifyWithFallback}.
 */
export function slugifyClient(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "") // combining marks left by NFKD
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 63)
    .replace(/-+$/, "");
}

/**
 * FNV-1a (32-bit) as 8 hex chars. Deterministic and dependency-free; used only
 * to keep two otherwise-identical fallback slugs apart, never for security.
 */
function fnv1a32(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, "0");
}

/**
 * {@link slugifyClient}, but never empty: falls back to `<prefix>-<digest>`.
 *
 * The digest is taken over the name, so two names that both slugify to nothing
 * get two different slugs. A constant fallback would hand every such name the
 * same slug and collide them into one record.
 */
export function slugifyWithFallback(name: string, prefix: string): string {
  return slugifyClient(name) || `${prefix}-${fnv1a32(name)}`;
}

export const SLUG_REGEX = /^[a-z0-9][a-z0-9-]{0,62}$/;

export function isValidSlug(s: string): boolean {
  return SLUG_REGEX.test(s);
}
