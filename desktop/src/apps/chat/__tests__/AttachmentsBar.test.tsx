import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AttachmentsBar } from "../AttachmentsBar";

describe("AttachmentsBar", () => {
  it("renders nothing for empty items", () => {
    const { container } = render(
      <AttachmentsBar items={[]} onRemove={vi.fn()} onRetry={vi.fn()} />
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders filename and calls onRemove when × is clicked", () => {
    const onRemove = vi.fn();
    render(
      <AttachmentsBar
        items={[{ id: "x1", filename: "doc.pdf", size: 2048 }]}
        onRemove={onRemove}
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByText("doc.pdf")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Remove doc\.pdf/i }));
    expect(onRemove).toHaveBeenCalledWith("x1");
  });
});
