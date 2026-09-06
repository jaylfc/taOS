import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ArchivedAgentRow } from "./ArchivedAgents";
import type { ArchivedAgent } from "./types";

// Force the mobile branch so we exercise the stacked layout.
vi.mock("@/hooks/use-is-mobile", () => ({ useIsMobile: () => true }));

const entry: ArchivedAgent = {
  id: "arch-1",
  archived_slug: "worker-nemotron",
  archived_at: "20260628T101500",
  original: {
    name: "worker-nemotron",
    display_name: "worker-nemotron-a12",
    color: "#ef4444",
    emoji: "🤖",
    framework: "hermes",
    model: "nvidia/nemotron-3",
  },
} as ArchivedAgent;

describe("ArchivedAgentRow (mobile)", () => {
  it("shows the full agent name, model, and archived date without starving the name", () => {
    render(<ArchivedAgentRow entry={entry} onRestore={vi.fn()} onPurge={vi.fn()} />);
    // The full name is present (mobile gives it its own line, not one char).
    expect(screen.getByText("worker-nemotron-a12")).toBeInTheDocument();
    expect(screen.getByText("nvidia/nemotron-3")).toBeInTheDocument();
    expect(screen.getByText(/archived/i)).toBeInTheDocument();
  });

  it("renders restore + delete with 44px mobile tap targets", () => {
    render(<ArchivedAgentRow entry={entry} onRestore={vi.fn()} onPurge={vi.fn()} />);
    const restore = screen.getByRole("button", { name: /restore/i });
    const del = screen.getByRole("button", { name: /permanently delete/i });
    expect(restore.className).toContain("h-11");
    expect(restore.className).toContain("w-11");
    expect(del.className).toContain("h-11");
    expect(del.className).toContain("w-11");
  });
});
