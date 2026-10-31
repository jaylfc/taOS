import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const { isShell, openUrl } = vi.hoisted(() => ({ isShell: vi.fn(), openUrl: vi.fn() }));
vi.mock("@/lib/mobile-shell", () => ({ isShellDevice: isShell, openUrlInShell: openUrl }));

import { installShellLinks, _resetShellLinksForTests } from "./shell-links";

let originalOpen: ReturnType<typeof vi.fn>;

function click(a: HTMLAnchorElement, init: MouseEventInit = {}) {
  document.body.appendChild(a);
  const ev = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0, ...init });
  a.dispatchEvent(ev);
  a.remove();
  return ev;
}

function anchor(href: string, target?: string) {
  const a = document.createElement("a");
  a.setAttribute("href", href);
  if (target) a.setAttribute("target", target);
  return a;
}

async function flush() {
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

describe("shell link routing", () => {
  beforeEach(() => {
    originalOpen = vi.fn(() => null);
    window.open = originalOpen as unknown as typeof window.open;
    isShell.mockReturnValue(true);
    openUrl.mockReset();
    openUrl.mockResolvedValue(true);
    installShellLinks();
  });

  afterEach(() => {
    _resetShellLinksForTests();
  });

  it("external target=_blank click routes to openUrlInShell and is prevented", () => {
    const ev = click(anchor("https://example.com/x?y=1", "_blank"));
    expect(openUrl).toHaveBeenCalledWith("https://example.com/x?y=1");
    expect(ev.defaultPrevented).toBe(true);
  });

  it("a click on a child of the anchor routes too", () => {
    const a = anchor("https://example.com/", "_blank");
    const span = document.createElement("span");
    a.appendChild(span);
    document.body.appendChild(a);
    const ev = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
    span.dispatchEvent(ev);
    a.remove();
    expect(openUrl).toHaveBeenCalledWith("https://example.com/");
  });

  it("same-origin links are untouched", () => {
    const ev = click(anchor(`${location.origin}/page`, "_blank"));
    expect(openUrl).not.toHaveBeenCalled();
    expect(ev.defaultPrevented).toBe(false);
    window.open(`${location.origin}/page`, "_blank");
    expect(originalOpen).toHaveBeenCalledTimes(1);
  });

  it("javascript: and mailto: are untouched", () => {
    for (const href of ["javascript:void(0)", "mailto:a@b.co"]) {
      const ev = click(anchor(href, "_blank"));
      expect(ev.defaultPrevented).toBe(false);
      window.open(href);
    }
    expect(openUrl).not.toHaveBeenCalled();
    expect(originalOpen).toHaveBeenCalledTimes(2);
  });

  it("links without a new-window target, and modified clicks, are untouched", () => {
    expect(click(anchor("https://example.com/")).defaultPrevented).toBe(false);
    expect(click(anchor("https://example.com/", "_self")).defaultPrevented).toBe(false);
    expect(click(anchor("https://example.com/", "_blank"), { ctrlKey: true }).defaultPrevented).toBe(false);
    expect(openUrl).not.toHaveBeenCalled();
  });

  it("window.open with an external URL routes and returns null", () => {
    expect(window.open("https://example.com/a", "_blank")).toBeNull();
    expect(openUrl).toHaveBeenCalledWith("https://example.com/a");
    expect(originalOpen).not.toHaveBeenCalled();
  });

  it("window.open into _self, _parent or _top navigates this window, not Firefox", () => {
    for (const target of ["_self", "_parent", "_top"]) {
      window.open("https://example.com/a", target);
    }
    expect(openUrl).not.toHaveBeenCalled();
    expect(originalOpen).toHaveBeenCalledTimes(3);
  });

  it("falls back to the original window.open when openUrlInShell resolves false", async () => {
    openUrl.mockResolvedValue(false);
    window.open("https://example.com/a", "_blank", "noopener");
    await flush();
    expect(originalOpen).toHaveBeenCalledWith("https://example.com/a", "_blank", "noopener");
  });

  it("falls back for a clicked link too", async () => {
    openUrl.mockResolvedValue(false);
    click(anchor("https://example.com/a", "_blank"));
    await flush();
    expect(originalOpen).toHaveBeenCalledWith("https://example.com/a", "_blank");
  });

  it("non-shell device: nothing is routed or prevented", () => {
    isShell.mockReturnValue(false);
    const ev = click(anchor("https://example.com/", "_blank"));
    window.open("https://example.com/", "_blank");
    expect(openUrl).not.toHaveBeenCalled();
    expect(ev.defaultPrevented).toBe(false);
    expect(originalOpen).toHaveBeenCalledTimes(1);
  });
});
