import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CodeBlock } from "./CodeBlock";

describe("CodeBlock", () => {
  it("renders the code text", () => {
    render(<CodeBlock code="const x = 1;" />);
    expect(screen.getByText("const x = 1;")).toBeInTheDocument();
  });

  it("shows a copy button with the correct aria-label", () => {
    render(<CodeBlock code="hello" />);
    const button = screen.getByRole("button", { name: /copy code/i });
    expect(button).toBeInTheDocument();
  });

  it("copies code to clipboard and shows Copied state", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<CodeBlock code="copy me" />);
    fireEvent.click(screen.getByRole("button", { name: /copy code/i }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("copy me"));
    expect(screen.getByRole("button", { name: /copied/i })).toBeInTheDocument();
  });
});
