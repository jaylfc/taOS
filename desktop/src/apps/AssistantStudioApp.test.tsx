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
