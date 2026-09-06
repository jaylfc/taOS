import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThreadIndicator } from "../ThreadIndicator";

describe("ThreadIndicator", () => {
  it("renders nothing for zero replies", () => {
    const { container } = render(<ThreadIndicator replyCount={0} onOpen={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });
  it("uses singular 'reply' for one", () => {
    render(<ThreadIndicator replyCount={1} onOpen={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Open thread/i })).toHaveTextContent("1 reply");
  });
  it("uses plural 'replies' for two+ and includes last reply relative time", () => {
    const past = Math.floor(Date.now() / 1000) - 120; // 2 min ago
    render(<ThreadIndicator replyCount={3} lastReplyAt={past} onOpen={vi.fn()} />);
    const btn = screen.getByRole("button", { name: /Open thread/i });
    expect(btn.textContent).toMatch(/3 replies/);
    expect(btn.textContent).toMatch(/2m ago/);
  });
});
