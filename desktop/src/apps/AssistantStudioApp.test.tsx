import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { AssistantStudioApp } from "./AssistantStudioApp";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

describe("AssistantStudioApp", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => [{ name: "hermes" }, { name: "other" }],
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders all 7 rail sections", async () => {
    render(<AssistantStudioApp windowId="win-1" />);
    // Drain the mount-time /api/agents fetch so its setState lands inside act().
    await waitFor(() =>
      expect(screen.queryByText("Loading agents...")).not.toBeInTheDocument(),
    );
    const nav = document.querySelector("nav[aria-label='Assistant Studio sections']");
    expect(nav).toBeInTheDocument();
    const railLabels = [
      "Overview",
      "Journal",
      "Calendar",
      "Tasks",
      "Comms",
      "Canvas",
      "Deliverables",
    ];
    for (const label of railLabels) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("switches visible panel when a rail button is clicked", async () => {
    render(<AssistantStudioApp windowId="win-1" />);
    // Drain the mount-time /api/agents fetch so its setState lands inside act().
    await waitFor(() =>
      expect(screen.queryByText("Loading agents...")).not.toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Journal" }));
    expect(screen.getByRole("heading", { name: "Journal" })).toBeInTheDocument();
  });

  it("renders PA selector with accessible label", async () => {
    render(<AssistantStudioApp windowId="win-1" />);
    const paSelect = screen.getByLabelText(
      "Select the agent to act as your personal assistant",
    );
    expect(paSelect).toBeInTheDocument();
    await waitFor(() => expect(paSelect.value).toBe("hermes"));
  });

  it("adds a journal entry", async () => {
    render(<AssistantStudioApp windowId="win-1" />);
    const paSelect = screen.getByLabelText(
      "Select the agent to act as your personal assistant",
    );
    await waitFor(() => expect(paSelect.value).toBe("hermes"));

    fireEvent.click(screen.getByRole("button", { name: "Journal" }));
    expect(screen.getByRole("heading", { name: "Journal" })).toBeInTheDocument();

    const textarea = screen.getByLabelText("New journal entry");
    fireEvent.change(textarea, { target: { value: "hello journal" } });
    fireEvent.click(screen.getByRole("button", { name: "Add entry" }));
    expect(screen.getByText("hello journal")).toBeInTheDocument();
  });
});

describe("PA change confirmation", () => {
  const agents = [
    { name: "hermes", display_name: "Hermes" },
    { name: "atlas", display_name: "Atlas" },
  ];

  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        String(url).includes("/api/agents")
          ? { ok: true, json: async () => agents }
          : { ok: true, json: async () => [] },
      ),
    );
  });

  async function openStudio() {
    render(<AssistantStudioApp />);
    return await screen.findByLabelText(
      "Select the agent to act as your personal assistant",
    );
  }

  it("does NOT commit the change until it is confirmed", async () => {
    localStorage.setItem("taos.assistantStudio.pa", "hermes");
    const select = await openStudio();

    fireEvent.change(select, { target: { value: "atlas" } });

    // The dialog is up and the stored PA is still the old one. This is the
    // assertion that goes red if the guard is removed: without it, the change
    // is written to localStorage synchronously on change.
    expect(
      screen.getByRole("dialog", { name: /change your personal assistant/i }),
    ).toBeTruthy();
    expect(localStorage.getItem("taos.assistantStudio.pa")).toBe("hermes");
  });

  it("keeps the old PA when cancelled", async () => {
    localStorage.setItem("taos.assistantStudio.pa", "hermes");
    const select = await openStudio();

    fireEvent.change(select, { target: { value: "atlas" } });
    fireEvent.click(screen.getByText("Keep current PA"));

    expect(localStorage.getItem("taos.assistantStudio.pa")).toBe("hermes");
    expect((select as HTMLSelectElement).value).toBe("hermes");
  });

  it("applies the new PA when confirmed", async () => {
    localStorage.setItem("taos.assistantStudio.pa", "hermes");
    const select = await openStudio();

    fireEvent.change(select, { target: { value: "atlas" } });
    fireEvent.click(screen.getByText("Change PA"));

    expect(localStorage.getItem("taos.assistantStudio.pa")).toBe("atlas");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("does NOT prompt when there is no PA yet — nothing to move away from", async () => {
    // Assigning a PA from an empty selection is a first assignment, not a
    // change, and must not interrupt the user.
    //
    // This has to reach the picker with `pa` EMPTY and then choose a REAL
    // agent. Selecting the empty option on its own returns through the `!name`
    // branch, so a test that stops there still passes with the
    // first-assignment guard deleted — it asserts nothing. Clearing first and
    // then picking "atlas" is the only path that runs through `!pa`, and it
    // goes red if that guard is removed.
    localStorage.setItem("taos.assistantStudio.pa", "hermes");
    const select = await openStudio();

    fireEvent.change(select, { target: { value: "" } });
    fireEvent.change(select, { target: { value: "atlas" } });

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(localStorage.getItem("taos.assistantStudio.pa")).toBe("atlas");
  });
});

describe("AssistantStudioApp theming", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        String(url).includes("/api/agents")
          ? { ok: true, json: async () => [{ name: "hermes" }, { name: "atlas" }] }
          : { ok: true, json: async () => [] },
      ),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // Every colour-bearing utility family, not just the handful this app happens
  // to use today. A guard narrower than the rule it states cannot fail on the
  // gaps: `outline-sky-500` and `shadow-zinc-900` are exactly as scheme-pinned
  // as `bg-zinc-800`, and an earlier revision of this test passed with one of
  // them live in the markup.
  const COLOUR_PROPS =
    "bg|text|border|ring|outline|shadow|placeholder|from|via|to|decoration|caret|accent|fill|stroke|divide";
  // Status hues (emerald/amber/red) are deliberately allowed: they carry
  // meaning, not chrome, and that is the convention across the other apps.
  const PINNED_FAMILIES = "zinc|sky|slate|gray|neutral|stone|blue|indigo|violet|purple|teal|cyan";
  const PALETTE_RE = new RegExp(`(^|:)(${COLOUR_PROPS})-(${PINNED_FAMILIES})-\\d`);

  // The light-scheme compatibility layer in tokens.css inverts a FIXED,
  // enumerated set of white/black overlay utilities. Anything outside that set
  // — notably arbitrary values like `bg-white/[0.04]`, which no `[class~=...]`
  // rule matches — stays white on a light background. Read the covered set out
  // of tokens.css itself rather than restating it here, so this guard tracks
  // the source of truth instead of drifting from a copy of it.
  // Read from disk, NOT via a `?raw` import: this project's vitest config does
  // not process CSS, so `import css from "...?raw"` resolves to an empty string
  // and would leave the covered set silently empty.
  const tokensCss = (() => {
    // Resolve from the working directory rather than `import.meta.url`, which
    // vitest does not hand us as a file: URL. Both candidates are tried so the
    // suite works whether it is run from desktop/ or from the repo root.
    for (const p of ["src/theme/tokens.css", "desktop/src/theme/tokens.css"]) {
      try {
        return readFileSync(resolve(process.cwd(), p), "utf8");
      } catch {
        /* try the next candidate */
      }
    }
    return "";
  })();
  const COVERED_OVERLAYS = new Set(
    Array.from(tokensCss.matchAll(/\[class~="([^"]+)"\]/g), (m) => m[1]),
  );
  // Guard the guard: an empty covered-set would flag every overlay (noisy) and,
  // worse, silently changes what this test means. Fail loudly instead.
  it("reads the light-scheme compatibility layer out of tokens.css", () => {
    expect(COVERED_OVERLAYS.size).toBeGreaterThan(10);
    expect(COVERED_OVERLAYS.has("bg-white/5")).toBe(true);
  });
  const OVERLAY_RE = /(^|:)(bg|text|border|ring|outline|shadow|divide|from|via|to)-(white|black)(\/|$)/;

  // Uncovered overlays DO reach this app's rendered tree, but they come from the
  // shared primitives (Button's secondary/outline/ghost variants, Card, Tabs,
  // Toolbar), which hardcode arbitrary values like `bg-white/[0.06]` that no
  // `[class~=...]` rule in tokens.css matches. That is a fleet-wide light-theme
  // gap affecting every app, tracked separately — fixing it here would change
  // rendering across the whole desktop from a Studio reskin. What this PR owns
  // is the Studio's OWN markup, so that is what is asserted, at source level.
  it("introduces no uncovered white/black overlay of its own", () => {
    const appSrc = (() => {
      for (const p of [
        "src/apps/AssistantStudioApp.tsx",
        "desktop/src/apps/AssistantStudioApp.tsx",
      ]) {
        try {
          return readFileSync(resolve(process.cwd(), p), "utf8");
        } catch {
          /* try the next candidate */
        }
      }
      return "";
    })();
    expect(appSrc.length).toBeGreaterThan(0);

    // Strip comments first: the file documents the very classes it avoids, and
    // prose about `bg-white/[0.04]` is not a rendered overlay.
    const code = appSrc
      .replace(/\/\*[\s\S]*?\*\//g, " ")
      .replace(/(^|[^:])\/\/[^\n]*/g, "$1 ");

    // Every whitespace/quote-delimited class token in the file, so an overlay
    // added anywhere in the markup is seen.
    const offenders = new Set<string>();
    for (const tok of code.split(/[\s"'`{}()]+/)) {
      if (OVERLAY_RE.test(tok) && !COVERED_OVERLAYS.has(tok)) offenders.add(tok);
    }
    expect(Array.from(offenders)).toEqual([]);
  });

  it("uses theme tokens, never scheme-pinned colours, on every rendered surface", async () => {
    const { container } = render(<AssistantStudioApp windowId="win-1" />);
    await waitFor(() =>
      expect(screen.queryByText("Loading agents...")).not.toBeInTheDocument(),
    );

    // Walk every section so each view's markup is actually inspected, not just
    // the Overview that happens to render first.
    const sections = ["Journal", "Calendar", "Tasks", "Comms", "Canvas", "Deliverables"];
    const offenders = new Set<string>();
    const scan = () => {
      for (const el of container.querySelectorAll<HTMLElement>("*")) {
        for (const cls of Array.from(el.classList)) {
          if (PALETTE_RE.test(cls)) offenders.add(cls);
        }
        // Inline colour literals bypass the class system entirely.
        const style = el.getAttribute("style") || "";
        if (/#[0-9a-fA-F]{3,8}\b|\brgba?\(/.test(style)) offenders.add(`style="${style}"`);
      }
    };
    scan();
    for (const label of sections) {
      fireEvent.click(screen.getByRole("button", { name: label }));
      scan();
    }

    expect(Array.from(offenders)).toEqual([]);
  });
});
