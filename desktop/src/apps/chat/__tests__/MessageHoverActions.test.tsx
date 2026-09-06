import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageHoverActions } from "../MessageHoverActions";

describe("MessageHoverActions", () => {
  it("calls onReact when the reaction button is clicked", () => {
    const onReact = vi.fn();
    render(<MessageHoverActions onReact={onReact} onReplyInThread={vi.fn()} onOverflow={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Add reaction/i }));
    expect(onReact).toHaveBeenCalled();
  });
  it("calls onReplyInThread when the reply button is clicked", () => {
    const onReply = vi.fn();
    render(<MessageHoverActions onReact={vi.fn()} onReplyInThread={onReply} onOverflow={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Reply in thread/i }));
    expect(onReply).toHaveBeenCalled();
  });
  it("calls onOverflow when the overflow button is clicked", () => {
    const onOverflow = vi.fn();
    render(<MessageHoverActions onReact={vi.fn()} onReplyInThread={vi.fn()} onOverflow={onOverflow} />);
    fireEvent.click(screen.getByRole("button", { name: /More/i }));
    expect(onOverflow).toHaveBeenCalled();
  });
});
