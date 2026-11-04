import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { PinnedMessagesPopover } from "../PinnedMessagesPopover";

describe("PinnedMessagesPopover", () => {
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
});