import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { HelpPanel } from "../HelpPanel";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("HelpPanel", () => {
  it("fetches and renders the markdown from /api/docs/chat-guide", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ markdown: "# Chat Guide\nHello world" }),
    }));

    render(<HelpPanel onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/Hello world/)).toBeInTheDocument());
    expect(screen.getByRole("dialog", { name: "Chat guide" })).toBeInTheDocument();
  });

  it("closes on Escape keypress", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ markdown: "guide content" }),
    }));
    const onClose = vi.fn();
    render(<HelpPanel onClose={onClose} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes on backdrop click", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ markdown: "guide content" }),
    }));
    const onClose = vi.fn();
    render(<HelpPanel onClose={onClose} />);
    const backdrop = screen.getByRole("dialog", { name: "Chat guide" });
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledOnce();
  });
});
