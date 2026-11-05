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

  it("renders dialog with max-w-[calc(100vw-1rem)] class when pins list is empty", () => {
    render(
      <PinnedMessagesPopover
        pins={[]}
        onJumpTo={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: /pinned messages/i });
    expect(dialog).toHaveClass("max-w-[calc(100vw-1rem)]");
  });

  it("container max-height is not bare 400px", () => {
    const pins = [{ id: "m1", author_id: "tom", content: "x", created_at: 123, pinned_by: "u", pinned_at: 200 }];
    render(<PinnedMessagesPopover pins={pins} onJumpTo={vi.fn()} onClose={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: /pinned messages/i });
    // Before fix: the dialog has max-h-[400px] class (bare 400px cap)
    // After fix: it should use a dynamic max-height like min(400px, 60dvh) via style or class
    // The test should fail before fix (has max-h-[400px]) and pass after fix (no bare 400px)
    expect(dialog).not.toHaveClass("max-h-[400px]");
  });
});
