import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PinRequestAffordance } from "../PinRequestAffordance";

describe("PinRequestAffordance", () => {
  it("calls onApprove when clicked", () => {
    const onApprove = vi.fn();
    render(<PinRequestAffordance authorId="tom" onApprove={onApprove} />);
    fireEvent.click(screen.getByRole("button", { name: /Pin this/i }));
    expect(onApprove).toHaveBeenCalled();
  });

  it("shows author name in label", () => {
    render(<PinRequestAffordance authorId="tom" onApprove={vi.fn()} />);
    expect(screen.getByText(/tom/)).toBeInTheDocument();
  });
});
