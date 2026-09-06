import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { AllThreadsList } from "../AllThreadsList";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("AllThreadsList", () => {
  it("shows empty state when no threads returned", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ threads: [] }),
    }));

    render(<AllThreadsList channelId="c1" onClose={vi.fn()} onJumpToThread={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("No threads yet.")).toBeInTheDocument());
  });

  it("renders thread rows with author, reply count, and preview", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        threads: [
          { id: "t1", author_id: "alice", content: "First thread message", reply_count: 3, last_reply_at: null },
          { id: "t2", author_id: "bob", content: "Second thread message", reply_count: 1, last_reply_at: null },
        ],
      }),
    }));

    render(<AllThreadsList channelId="c1" onClose={vi.fn()} onJumpToThread={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("First thread message")).toBeInTheDocument());
    expect(screen.getByText("Second thread message")).toBeInTheDocument();
    expect(screen.getByText("@alice")).toBeInTheDocument();
    expect(screen.getByText("3 replies")).toBeInTheDocument();
  });
});
