import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { AssistantStudioApp } from "./AssistantStudioApp";

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
