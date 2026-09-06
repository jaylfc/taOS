import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PinBadge } from "../PinBadge";

describe("PinBadge", () => {
  it("renders null when count is 0", () => {
    const { container } = render(<PinBadge count={0} onClick={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders count when > 0", () => {
    render(<PinBadge count={3} onClick={vi.fn()} />);
    expect(screen.getByRole("button")).toHaveTextContent("3");
  });

  it("fires onClick", () => {
    const onClick = vi.fn();
    render(<PinBadge count={1} onClick={onClick} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalled();
  });
});
