import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";

import { TextEditorApp } from "../TextEditorApp";
import { startDrag, endDrag } from "@/shell/dnd/dnd-bus";

describe("TextEditorApp", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.spyOn(window, "fetch").mockResolvedValue({ ok: true, json: async () => ({}) } as Response);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    endDrag();
  });

  it("creates a note and shows it in the sidebar (crypto.randomUUID available)", () => {
    render(<TextEditorApp windowId="w1" />);

    fireEvent.click(screen.getByRole("button", { name: /create your first note/i }));

    // The new note becomes the active/selected entry, with a delete affordance
    // in the sidebar keyed to its (unique) title.
    expect(screen.getByRole("button", { name: "Delete New Note" })).toBeInTheDocument();

    // Persisted to localStorage so it survives a reload.
    const stored = JSON.parse(localStorage.getItem("tinyagentos-notes") ?? "[]");
    expect(stored).toHaveLength(1);
    expect(stored[0].content).toContain("New Note");
  });

  it("still creates a note when crypto.randomUUID is unavailable (insecure-context regression, #1584)", () => {
    // taOS is normally served over plain http (e.g. http://taos.local:6969),
    // which is not a "secure context" per the spec, so window.crypto.randomUUID
    // is undefined there. Simulate that and confirm note creation still works
    // instead of silently throwing inside the click handler.
    // randomUUID lives on the Crypto prototype, not as an own property, so
    // `delete crypto.randomUUID` is a silent no-op — shadow it with an own
    // property instead to faithfully simulate the missing API.
    const originalRandomUUID = crypto.randomUUID;
    Object.defineProperty(crypto, "randomUUID", { value: undefined, configurable: true });

    try {
      render(<TextEditorApp windowId="w1" />);

      fireEvent.click(screen.getByRole("button", { name: /create your first note/i }));

      expect(screen.getAllByText("New Note").length).toBeGreaterThan(0);
      const stored = JSON.parse(localStorage.getItem("tinyagentos-notes") ?? "[]");
      expect(stored).toHaveLength(1);
      expect(typeof stored[0].id).toBe("string");
      expect(stored[0].id.length).toBeGreaterThan(0);
    } finally {
      Object.defineProperty(crypto, "randomUUID", { value: originalRandomUUID, configurable: true });
    }
  });

  it("keeps the same CodeMirror instance and focus through continuous typing, even when crypto.randomUUID is unavailable (#1596)", () => {
    // #1596: the editor accepted exactly one character then lost focus, 100%
    // reproducible over plain http. Drive several sequential edits through the
    // exact same dispatch -> updateListener -> onChange chain a real keystroke
    // uses (via the editor's own insertAtCursor, triggered here through the
    // drop-target plumbing) and confirm the underlying view survives them all.
    const originalRandomUUID = crypto.randomUUID;
    Object.defineProperty(crypto, "randomUUID", { value: undefined, configurable: true });

    try {
      const { container } = render(<TextEditorApp windowId="w1" />);
      fireEvent.click(screen.getByRole("button", { name: /create your first note/i }));

      const cmEditorBefore = container.querySelector(".cm-editor");
      expect(cmEditorBefore).toBeTruthy();
      const dropTarget = cmEditorBefore!.parentElement!.parentElement!;

      const chars = ["h", "e", "l", "l", "o"];
      chars.forEach((ch, i) => {
        startDrag({ kind: "knowledge", id: `k${i}`, title: ch });
        fireEvent.drop(dropTarget);
      });

      // Same DOM node throughout: the view was never destroyed and recreated,
      // so the browser's focus was never yanked out from under the user.
      const cmEditorAfter = container.querySelector(".cm-editor");
      expect(cmEditorAfter).toBe(cmEditorBefore);
      const cmContent = container.querySelector(".cm-content");
      expect(document.activeElement).toBe(cmContent);

      // A persistent view tracks the cursor across edits, so the characters
      // land in typed order ("hello"). Recreating the view on every keystroke
      // (the #1596 bug) resets the cursor to the document start each time,
      // which prepends each new character and reverses the typed order.
      const stored = JSON.parse(localStorage.getItem("tinyagentos-notes") ?? "[]");
      expect(stored[0].content.startsWith("hello")).toBe(true);
    } finally {
      Object.defineProperty(crypto, "randomUUID", { value: originalRandomUUID, configurable: true });
    }
  });
});
