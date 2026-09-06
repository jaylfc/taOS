import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PinnedMessagesPopover } from "../PinnedMessagesPopover";

describe("PinnedMessagesPopover", () => {
  it("shows empty state when pins is []", () => {
    render(<PinnedMessagesPopover pins={[]} onJumpTo={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText(/no pinned messages/i)).toBeInTheDocument();
  });

  it("renders a pinned message", () => {
    const pins = [{
      id: "m1", author_id: "tom", content: "important",
      created_at: 123, pinned_by: "user:jay", pinned_at: 200,
    }];
    render(<PinnedMessagesPopover pins={pins} onJumpTo={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText("important")).toBeInTheDocument();
  });

  it("fires onJumpTo with message id", () => {
    const onJumpTo = vi.fn();
    const pins = [{ id: "m1", author_id: "tom", content: "x", created_at: 123, pinned_by: "u", pinned_at: 200 }];
    render(<PinnedMessagesPopover pins={pins} onJumpTo={onJumpTo} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Jump to/i }));
    expect(onJumpTo).toHaveBeenCalledWith("m1");
  });
});
