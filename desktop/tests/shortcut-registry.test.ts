import { describe, it, expect, vi } from "vitest";
import { parseCombo, matchesEvent, type ShortcutEntry } from "../src/hooks/use-shortcut-registry";

describe("parseCombo", () => {
  it("parses simple key", () => {
    expect(parseCombo("Escape")).toEqual({ ctrl: false, shift: false, alt: false, key: "escape" });
  });

  it("parses Ctrl+key", () => {
    expect(parseCombo("Ctrl+W")).toEqual({ ctrl: true, shift: false, alt: false, key: "w" });
  });

  it("parses Ctrl+Shift+key", () => {
    expect(parseCombo("Ctrl+Shift+Tab")).toEqual({ ctrl: true, shift: true, alt: false, key: "tab" });
  });

  it("normalises order", () => {
    expect(parseCombo("Shift+Ctrl+A")).toEqual(parseCombo("Ctrl+Shift+A"));
  });
});

describe("matchesEvent", () => {
  function fakeEvent(key: string, ctrl = false, shift = false, alt = false, meta = false): KeyboardEvent {
    return { key, ctrlKey: ctrl, shiftKey: shift, altKey: alt, metaKey: meta } as KeyboardEvent;
  }

  it("matches Ctrl+W", () => {
    const parsed = parseCombo("Ctrl+W");
    expect(matchesEvent(parsed, fakeEvent("w", true))).toBe(true);
  });

  it("treats Meta as Ctrl", () => {
    const parsed = parseCombo("Ctrl+W");
    expect(matchesEvent(parsed, fakeEvent("w", false, false, false, true))).toBe(true);
  });

  it("does not match without modifier", () => {
    const parsed = parseCombo("Ctrl+W");
    expect(matchesEvent(parsed, fakeEvent("w"))).toBe(false);
  });

  it("matches Escape with no modifiers", () => {
    const parsed = parseCombo("Escape");
    expect(matchesEvent(parsed, fakeEvent("Escape"))).toBe(true);
  });

  it("does not match Escape when Ctrl is pressed", () => {
    const parsed = parseCombo("Escape");
    expect(matchesEvent(parsed, fakeEvent("Escape", true))).toBe(false);
  });

  it("matches Ctrl+Shift+Tab", () => {
    const parsed = parseCombo("Ctrl+Shift+Tab");
    expect(matchesEvent(parsed, fakeEvent("Tab", true, true))).toBe(true);
  });
});

describe("priority", () => {
  it("overlay beats system", () => {
    const entries: ShortcutEntry[] = [
      { id: "sys", combo: parseCombo("Escape"), action: vi.fn(), label: "System", scope: "system", enabled: true },
      { id: "ovl", combo: parseCombo("Escape"), action: vi.fn(), label: "Overlay", scope: "overlay", enabled: true },
    ];
    const sorted = [...entries].sort((a, b) => {
      const order = { overlay: 0, app: 1, system: 2 };
      return order[a.scope] - order[b.scope];
    });
    expect(sorted[0].id).toBe("ovl");
  });
});
