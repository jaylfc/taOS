/**
 * Link routing for the taOSmobile handset: a link an app opens in a new window
 * goes to Firefox through taos-shelld instead of a second kiosk window.
 *
 * Installed once at boot (the desktop and the standalone app window). The
 * device-class check is made per event, not at install, because the class is
 * fetched after boot; on any other device nothing here does anything.
 */
import { isShellDevice, openUrlInShell } from "@/lib/mobile-shell";

let installed: { open: typeof window.open; onClick: (e: MouseEvent) => void } | null = null;

/** The absolute http(s) URL when `raw` leaves this origin, else null. */
function externalUrl(raw: string | URL | undefined | null): string | null {
  if (raw === undefined || raw === null || raw === "") return null;
  try {
    const u = new URL(String(raw), location.href);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    return u.origin === location.origin ? null : u.href;
  } catch {
    return null;
  }
}

export function installShellLinks(): void {
  if (installed || typeof window === "undefined") return;
  const open = window.open;

  window.open = function (this: unknown, url?: string | URL, target?: string, features?: string) {
    const inPlace = target === "_self" || target === "_parent" || target === "_top";
    const href = isShellDevice() && !inPlace ? externalUrl(url) : null;
    if (!href) return open.call(window, url, target, features);
    void openUrlInShell(href).then((ok) => {
      if (!ok) open.call(window, url, target, features);
    });
    return null;
  } as typeof window.open;

  const onClick = (e: MouseEvent) => {
    if (e.defaultPrevented || e.button !== 0) return;
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    if (!isShellDevice()) return;
    const a = (e.target as Element | null)?.closest?.("a");
    if (!a) return;
    const target = a.getAttribute("target");
    if (!target || target === "_self" || target === "_parent" || target === "_top") return;
    const href = externalUrl(a.getAttribute("href"));
    if (!href) return;
    e.preventDefault();
    void openUrlInShell(href).then((ok) => {
      if (!ok) open.call(window, href, target);
    });
  };
  document.addEventListener("click", onClick, true);
  installed = { open, onClick };
}

/** Test-only: put window.open back and drop the click listener. */
export function _resetShellLinksForTests(): void {
  if (!installed) return;
  window.open = installed.open;
  document.removeEventListener("click", installed.onClick, true);
  installed = null;
}
