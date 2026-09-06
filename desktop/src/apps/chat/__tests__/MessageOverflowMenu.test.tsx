import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageOverflowMenu } from "../MessageOverflowMenu";

describe("MessageOverflowMenu", () => {
  const baseProps = {
    onEdit: vi.fn(),
    onDelete: vi.fn(),
    onCopyLink: vi.fn(),
    onPin: vi.fn(),
    onMarkUnread: vi.fn(),
  };

  it("shows Edit+Delete when isOwn is true", () => {
    render(<MessageOverflowMenu isOwn={true} isHuman={true} {...baseProps} />);
    expect(screen.getByRole("menuitem", { name: /Edit/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Delete/i })).toBeInTheDocument();
  });

  it("hides Edit+Delete when isOwn is false", () => {
    render(<MessageOverflowMenu isOwn={false} isHuman={true} {...baseProps} />);
    expect(screen.queryByRole("menuitem", { name: /Edit/i })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: /Delete/i })).toBeNull();
  });

  it("hides Pin when isHuman is false", () => {
    render(<MessageOverflowMenu isOwn={false} isHuman={false} {...baseProps} />);
    expect(screen.queryByRole("menuitem", { name: /^Pin$/i })).toBeNull();
  });

  it("fires onEdit when Edit is clicked", () => {
    const onEdit = vi.fn();
    render(<MessageOverflowMenu isOwn={true} isHuman={true} {...baseProps} onEdit={onEdit} />);
    fireEvent.click(screen.getByRole("menuitem", { name: /Edit/i }));
    expect(onEdit).toHaveBeenCalled();
  });

  it("shows Unpin when isPinned is true", () => {
    render(<MessageOverflowMenu isOwn={false} isHuman={true} isPinned={true} {...baseProps} />);
    expect(screen.getByRole("menuitem", { name: /Unpin/i })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /^Pin$/i })).toBeNull();
  });
});
