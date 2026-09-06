import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { CodeBlock } from "../CodeBlock";

describe("CodeBlock", () => {
  let writeText: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders multi-line code exactly, preserving newlines", () => {
    const code = "line one\nline two\nline three";
    const { container } = render(<CodeBlock code={code} />);
    const pre = container.querySelector("pre");
    expect(pre?.textContent).toBe(code);
  });

  it("has a copy button with an accessible name matching /copy/i", () => {
    render(<CodeBlock code="x" />);
    expect(screen.getByRole("button", { name: /copy/i })).toBeInTheDocument();
  });

  it("copies the exact code string on click", async () => {
    const code = "const a = 1;\nconst b = 2;";
    render(<CodeBlock code={code} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /copy/i }));
    });
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith(code);
  });

  it("flips the label to copied then reverts after the timeout", async () => {
    vi.useFakeTimers();
    try {
      render(<CodeBlock code="x" />);
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: /copy/i }));
      });
      expect(screen.getByRole("button", { name: /copied/i })).toBeInTheDocument();
      act(() => {
        vi.advanceTimersByTime(1600);
      });
      // aria-label reverts to "Copy code" (which does not match /copied/i)
      expect(screen.getByRole("button", { name: /copy code/i })).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders long single-line code without throwing, inside an overflow container", () => {
    const code = "x".repeat(500);
    const { container } = render(<CodeBlock code={code} />);
    expect(container.querySelector(".overflow-x-auto")).not.toBeNull();
    expect(container.querySelector("pre")?.textContent).toBe(code);
  });

  it("renders empty code without crashing", () => {
    const { container } = render(<CodeBlock code="" />);
    expect(container.querySelector("pre")).not.toBeNull();
  });
});
